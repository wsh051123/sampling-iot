import sys
import time
import re
import struct
from collections import deque
from datetime import datetime
import threading

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QTextEdit, QGroupBox, QGridLayout, QMessageBox,
                             QFileDialog, QLineEdit, QDialog, QFormLayout)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QCursor

import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, AutoMinorLocator, FuncFormatter, ScalarFormatter
import matplotlib.patches as mpatches

# 设置matplotlib中文字体
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# ==================== 通信协议定义 ====================
from enum import Enum

class Command(Enum):
    CMD_PING = 0x01
    CMD_SINGLE_READ = 0x02
    CMD_CONTINUOUS_START = 0x03
    CMD_CONTINUOUS_STOP = 0x04
    CMD_CONFIG_PGA = 0x05
    CMD_CONFIG_RATE = 0x06
    CMD_CONFIG_VREF = 0x07
    CMD_GET_STATUS = 0x08
    CMD_ADC_DATA = 0x09
    CMD_ACK = 0x80
    CMD_ERROR = 0x81

class ProtocolHandler:
    def __init__(self):
        self.START_BYTE_1 = 0xAA
        self.START_BYTE_2 = 0xBB
        self.MAX_DATA_LENGTH = 32
        self.rx_buffer = bytearray()
        self.frame_started = False
        self.expected_length = 0
        self.data_received = 0
        
    def calculate_checksum(self, data):
        """计算校验和"""
        return sum(data) & 0xFF
    
    def build_frame(self, command, data=b''):
        """构建协议帧"""
        if len(data) > self.MAX_DATA_LENGTH:
            raise ValueError("Data too long")
        
        frame = bytearray()
        frame.append(self.START_BYTE_1)
        frame.append(self.START_BYTE_2)
        frame.append(len(data))
        frame.append(command.value)
        frame.extend(data)
        
        # 计算校验和（从长度到数据结束）
        checksum_data = bytearray()
        checksum_data.append(len(data))
        checksum_data.append(command.value)
        checksum_data.extend(data)
        checksum = self.calculate_checksum(checksum_data)
        frame.append(checksum)
        
        return frame
    
    def parse_frame(self, data):
        """解析接收到的帧"""
        if len(data) < 5:  # 最小帧长度
            return None, None
        
        if data[0] != self.START_BYTE_1 or data[1] != self.START_BYTE_2:
            return None, None
        
        data_length = data[2]
        command_value = data[3]
        frame_data = data[4:4 + data_length]
        received_checksum = data[4 + data_length]
        
        # 验证校验和
        checksum_data = data[2:4 + data_length]
        calculated_checksum = self.calculate_checksum(checksum_data)
        
        if calculated_checksum != received_checksum:
            return None, None
        
        try:
            command = Command(command_value)
            return command, frame_data
        except ValueError:
            return None, None
    
    def process_received_data(self, new_data, callback):
        """
        处理接收到的数据，解析帧并调用回调函数
        callback: function(command, data)
        返回成功解析的帧数量
        """
        frames_parsed = 0
        self.rx_buffer.extend(new_data)
        
        while len(self.rx_buffer) >= 5:  # 至少需要起始符+长度+命令字
            # 查找帧起始
            if not self.frame_started:
                start_index = -1
                for i in range(len(self.rx_buffer) - 1):
                    if self.rx_buffer[i] == self.START_BYTE_1 and self.rx_buffer[i+1] == self.START_BYTE_2:
                        start_index = i
                        break
                
                if start_index >= 0:
                    # 移除起始符之前的数据
                    self.rx_buffer = self.rx_buffer[start_index:]
                    self.frame_started = True
                    self.expected_length = 0
                    self.data_received = 0
                else:
                    # 没有找到起始符，清空缓冲区
                    self.rx_buffer.clear()
                    break
            
            if self.frame_started:
                # 检查是否有足够的数据来解析长度
                if len(self.rx_buffer) < 4:
                    break
                    
                if self.expected_length == 0:
                    self.expected_length = self.rx_buffer[2]
                    total_frame_length = 5 + self.expected_length  # 起始符2 + 长度1 + 命令1 + 数据N + 校验和1
                    
                    if self.expected_length > self.MAX_DATA_LENGTH:
                        # 无效长度，重新同步
                        self.frame_started = False
                        self.rx_buffer = self.rx_buffer[1:]
                        continue
                
                total_frame_length = 5 + self.expected_length
                
                if len(self.rx_buffer) >= total_frame_length:
                    # 完整帧已接收
                    frame_data = self.rx_buffer[:total_frame_length]
                    command, data = self.parse_frame(frame_data)
                    
                    if command is not None:
                        callback(command, data)
                        frames_parsed += 1
                    
                    # 移除已处理的数据
                    self.rx_buffer = self.rx_buffer[total_frame_length:]
                    self.frame_started = False
                    self.expected_length = 0
                else:
                    # 数据不足，等待更多数据
                    break
        
        return frames_parsed

# ==================== 串口线程 ====================
class SerialThread(QThread):
    """串口读取线程"""
    data_received = pyqtSignal(bytes)  # 修改为bytes类型
    error_occurred = pyqtSignal(str)
    
    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True
        
    def run(self):
        """线程运行函数"""
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting > 0:
                    # 读取所有可用数据
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    if data:
                        self.data_received.emit(data)
            except Exception as e:
                if self.running:
                    self.error_occurred.emit(f"读取错误: {str(e)}")
                break
            time.sleep(0.01)
    
    def stop(self):
        """停止线程"""
        self.running = False

# ==================== 数据分析窗口 ====================
class DataAnalysisWindow(QDialog):
    """数据分析窗口"""
    def __init__(self, data_x, data_y, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据分析")
        self.setGeometry(100, 100, 1400, 900)
        
        # 保存原始数据
        self.original_data_x = list(data_x)
        self.original_data_y = list(data_y)
        self.data_x = list(data_x)
        self.data_y = list(data_y)
        
        # 坐标范围
        self.x_min = min(self.data_x) if self.data_x else 0
        self.x_max = max(self.data_x) if self.data_x else 1
        self.y_min = min(self.data_y) if self.data_y else 0
        self.y_max = max(self.data_y) if self.data_y else 1
        
        # 鼠标悬停相关
        self.cursor_annotation = None
        self.cursor_vline = None
        self.cursor_hline = None
        
        # 缩放相关
        self.zoom_mode = False  # 是否处于缩放模式
        self.zoom_rect = None  # 缩放矩形
        self.zoom_start = None  # 缩放起始点
        self.press_event = None  # 鼠标按下事件
        
        self.init_ui()
        self.update_plot()
        
    def init_ui(self):
        """初始化界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        
        # 顶部控制面板
        control_panel = QGroupBox("坐标轴控制")
        control_layout = QGridLayout()
        control_layout.setSpacing(10)
        
        # X轴控制
        control_layout.addWidget(QLabel("X轴范围:"), 0, 0)
        control_layout.addWidget(QLabel("最小值:"), 0, 1)
        self.x_min_input = QLineEdit(f"{self.x_min:.2f}")
        self.x_min_input.setMaximumWidth(100)
        control_layout.addWidget(self.x_min_input, 0, 2)
        
        control_layout.addWidget(QLabel("最大值:"), 0, 3)
        self.x_max_input = QLineEdit(f"{self.x_max:.2f}")
        self.x_max_input.setMaximumWidth(100)
        control_layout.addWidget(self.x_max_input, 0, 4)
        
        # Y轴控制
        control_layout.addWidget(QLabel("Y轴范围:"), 1, 0)
        control_layout.addWidget(QLabel("最小值:"), 1, 1)
        self.y_min_input = QLineEdit(f"{self.y_min:.2f}")
        self.y_min_input.setMaximumWidth(100)
        control_layout.addWidget(self.y_min_input, 1, 2)
        
        control_layout.addWidget(QLabel("最大值:"), 1, 3)
        self.y_max_input = QLineEdit(f"{self.y_max:.2f}")
        self.y_max_input.setMaximumWidth(100)
        control_layout.addWidget(self.y_max_input, 1, 4)
        
        # 按钮组
        btn_layout = QHBoxLayout()
        
        apply_btn = QPushButton("应用范围")
        apply_btn.setMaximumWidth(100)
        apply_btn.clicked.connect(self.apply_range)
        btn_layout.addWidget(apply_btn)
        
        reset_btn = QPushButton("重置范围")
        reset_btn.setMaximumWidth(100)
        reset_btn.clicked.connect(self.reset_range)
        btn_layout.addWidget(reset_btn)
        
        auto_fit_btn = QPushButton("自动适配")
        auto_fit_btn.setMaximumWidth(100)
        auto_fit_btn.clicked.connect(self.auto_fit)
        btn_layout.addWidget(auto_fit_btn)
        
        # 添加缩放按钮
        self.zoom_btn = QPushButton("🔍 启用缩放")
        self.zoom_btn.setMaximumWidth(100)
        self.zoom_btn.setCheckable(True)
        self.zoom_btn.clicked.connect(self.toggle_zoom_mode)
        self.zoom_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:checked {
                background-color: #FF5722;
            }
        """)
        btn_layout.addWidget(self.zoom_btn)
        
        btn_layout.addStretch()
        
        control_layout.addLayout(btn_layout, 2, 0, 1, 5)
        control_panel.setLayout(control_layout)
        main_layout.addWidget(control_panel)
        
        # 信息显示标签
        info_layout = QHBoxLayout()
        self.coord_label = QLabel("鼠标坐标: --")
        self.coord_label.setStyleSheet("QLabel { font-size: 11pt; color: #2196F3; font-weight: bold; }")
        info_layout.addWidget(self.coord_label)
        
        self.stats_label = QLabel(f"数据点数: {len(self.data_x)}")
        self.stats_label.setStyleSheet("QLabel { font-size: 10pt; color: #666; }")
        info_layout.addStretch()
        info_layout.addWidget(self.stats_label)
        main_layout.addLayout(info_layout)
        
        # 绘图区域
        self.fig = Figure(figsize=(14, 8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel('时间 (秒)', fontsize=12)
        self.ax.set_ylabel('ADC 值', fontsize=12)
        self.ax.set_title('数据分析视图', fontsize=14, fontweight='bold')
        self.ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.8)
        self.ax.grid(True, which='minor', alpha=0.1, linestyle=':', linewidth=0.5)
        
        self.line, = self.ax.plot([], [], 'b-', linewidth=1.5, antialiased=True)
        self.canvas = FigureCanvas(self.fig)
        
        # 连接鼠标事件
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.canvas.mpl_connect('axes_leave_event', self.on_mouse_leave)
        self.canvas.mpl_connect('button_press_event', self.on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
        
        main_layout.addWidget(self.canvas)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        
        export_btn = QPushButton("导出数据")
        export_btn.clicked.connect(self.export_data)
        bottom_layout.addWidget(export_btn)
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        bottom_layout.addStretch()
        bottom_layout.addWidget(close_btn)
        
        main_layout.addLayout(bottom_layout)
        
    def update_plot(self):
        """更新图形"""
        try:
            if not self.data_x or not self.data_y:
                return
            
            # 根据范围过滤数据
            filtered_indices = [
                i for i, (x, y) in enumerate(zip(self.data_x, self.data_y))
                if self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max
            ]
            
            if not filtered_indices:
                self.line.set_data([], [])
                self.canvas.draw()
                return
            
            display_x = [self.data_x[i] for i in filtered_indices]
            display_y = [self.data_y[i] for i in filtered_indices]
            
            self.line.set_data(display_x, display_y)
            
            # 设置坐标轴范围
            x_margin = (self.x_max - self.x_min) * 0.02
            y_margin = (self.y_max - self.y_min) * 0.02
            
            self.ax.set_xlim(self.x_min - x_margin, self.x_max + x_margin)
            self.ax.set_ylim(self.y_min - y_margin, self.y_max + y_margin)
            
            # 更新统计信息
            self.stats_label.setText(
                f"数据点数: {len(display_x)} / {len(self.original_data_x)} | "
                f"X范围: [{self.x_min:.2f}, {self.x_max:.2f}] | "
                f"Y范围: [{self.y_min:.2f}, {self.y_max:.2f}]"
            )
            
            self.canvas.draw()
            
        except Exception as e:
            print(f"更新图形错误: {e}")
    
    def apply_range(self):
        """应用用户设置的范围"""
        try:
            x_min = float(self.x_min_input.text())
            x_max = float(self.x_max_input.text())
            y_min = float(self.y_min_input.text())
            y_max = float(self.y_max_input.text())
            
            if x_min >= x_max:
                QMessageBox.warning(self, "错误", "X轴最小值必须小于最大值")
                return
            
            if y_min >= y_max:
                QMessageBox.warning(self, "错误", "Y轴最小值必须小于最大值")
                return
            
            self.x_min = x_min
            self.x_max = x_max
            self.y_min = y_min
            self.y_max = y_max
            
            self.update_plot()
            
        except ValueError:
            QMessageBox.warning(self, "错误", "请输入有效的数值")
    
    def reset_range(self):
        """重置到原始数据范围"""
        self.x_min = min(self.original_data_x) if self.original_data_x else 0
        self.x_max = max(self.original_data_x) if self.original_data_x else 1
        self.y_min = min(self.original_data_y) if self.original_data_y else 0
        self.y_max = max(self.original_data_y) if self.original_data_y else 1
        
        self.x_min_input.setText(f"{self.x_min:.2f}")
        self.x_max_input.setText(f"{self.x_max:.2f}")
        self.y_min_input.setText(f"{self.y_min:.2f}")
        self.y_max_input.setText(f"{self.y_max:.2f}")
        
        self.update_plot()
    
    def auto_fit(self):
        """自动适配当前可见数据"""
        if not self.data_x or not self.data_y:
            return
        
        # 计算当前范围内的数据
        filtered_data = [(x, y) for x, y in zip(self.data_x, self.data_y)
                        if self.x_min <= x <= self.x_max]
        
        if not filtered_data:
            return
        
        y_values = [y for _, y in filtered_data]
        self.y_min = min(y_values)
        self.y_max = max(y_values)
        
        self.y_min_input.setText(f"{self.y_min:.2f}")
        self.y_max_input.setText(f"{self.y_max:.2f}")
        
        self.update_plot()
    
    def on_mouse_move(self, event):
        """鼠标移动事件 - 显示最近点的坐标或绘制缩放框"""
        if event.inaxes != self.ax:
            return
        
        # 如果处于缩放模式且正在拖动
        if self.zoom_mode and self.press_event is not None:
            self.draw_zoom_rect(event)
            return
        
        # 正常模式：显示坐标
        if not self.data_x or not self.data_y:
            return
        
        # 获取鼠标位置
        mouse_x = event.xdata
        mouse_y = event.ydata
        
        if mouse_x is None or mouse_y is None:
            return
        
        # 找到最近的数据点
        min_dist = float('inf')
        closest_x = None
        closest_y = None
        
        # 计算显示范围的缩放因子（用于归一化距离计算）
        x_range = self.x_max - self.x_min
        y_range = self.y_max - self.y_min
        
        for x, y in zip(self.data_x, self.data_y):
            if self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max:
                # 归一化距离计算
                dx = (x - mouse_x) / x_range if x_range > 0 else 0
                dy = (y - mouse_y) / y_range if y_range > 0 else 0
                dist = dx**2 + dy**2
                
                if dist < min_dist:
                    min_dist = dist
                    closest_x = x
                    closest_y = y
        
        # 如果找到的点距离鼠标太远，不显示
        if min_dist > 0.001:  # 阈值可调整
            self.coord_label.setText("鼠标坐标: --")
            self.clear_cursor()
            return
        
        if closest_x is not None and closest_y is not None:
            # 更新坐标显示
            self.coord_label.setText(
                f"鼠标坐标: X = {closest_x:.4f} 秒, Y = {closest_y:.2f}"
            )
            
            # 绘制十字光标
            self.draw_cursor(closest_x, closest_y)
    
    def on_mouse_leave(self, event):
        """鼠标离开图形区域"""
        self.coord_label.setText("鼠标坐标: --")
        self.clear_cursor()
    
    def draw_cursor(self, x, y):
        """绘制十字光标和标注"""
        # 清除旧的光标
        self.clear_cursor()
        
        # 绘制十字线
        self.cursor_vline = self.ax.axvline(x, color='red', linestyle='--', linewidth=0.8, alpha=0.7)
        self.cursor_hline = self.ax.axhline(y, color='red', linestyle='--', linewidth=0.8, alpha=0.7)
        
        # 绘制标注点
        self.cursor_annotation = self.ax.plot(x, y, 'ro', markersize=8, alpha=0.7)[0]
        
        self.canvas.draw_idle()
    
    def clear_cursor(self):
        """清除光标"""
        if self.cursor_vline:
            self.cursor_vline.remove()
            self.cursor_vline = None
        
        if self.cursor_hline:
            self.cursor_hline.remove()
            self.cursor_hline = None
        
        if self.cursor_annotation:
            self.cursor_annotation.remove()
            self.cursor_annotation = None
        
        self.canvas.draw_idle()
    
    def toggle_zoom_mode(self):
        """切换缩放模式"""
        self.zoom_mode = self.zoom_btn.isChecked()
        
        if self.zoom_mode:
            self.zoom_btn.setText("🔍 缩放模式")
            self.coord_label.setText("缩放模式：按住鼠标左键拖动选择区域进行放大")
            # 改变鼠标光标
            self.canvas.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self.zoom_btn.setText("🔍 启用缩放")
            self.coord_label.setText("鼠标坐标: --")
            # 恢复默认光标
            self.canvas.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            # 清除缩放矩形
            if self.zoom_rect:
                self.zoom_rect.remove()
                self.zoom_rect = None
                self.canvas.draw_idle()
    
    def on_mouse_press(self, event):
        """鼠标按下事件"""
        if not self.zoom_mode or event.inaxes != self.ax:
            return
        
        if event.button == 1:  # 左键
            self.press_event = event
            self.zoom_start = (event.xdata, event.ydata)
    
    def on_mouse_release(self, event):
        """鼠标释放事件"""
        if not self.zoom_mode or event.inaxes != self.ax or self.press_event is None:
            return
        
        if event.button == 1:  # 左键
            # 清除缩放矩形
            if self.zoom_rect:
                self.zoom_rect.remove()
                self.zoom_rect = None
            
            # 计算选择的区域
            x0, y0 = self.zoom_start
            x1, y1 = event.xdata, event.ydata
            
            if x0 is None or y0 is None or x1 is None or y1 is None:
                self.press_event = None
                return
            
            # 确保坐标顺序正确
            x_min = min(x0, x1)
            x_max = max(x0, x1)
            
            # 检查是否选择了有效区域（避免单点击）
            if abs(x_max - x_min) < (self.x_max - self.x_min) * 0.01:
                self.press_event = None
                self.canvas.draw_idle()
                return
            
            # 🔧 自动适配Y轴：根据选定X范围内的实际数据计算Y范围
            # 找出X范围内的所有数据点
            y_values_in_range = [y for x, y in zip(self.data_x, self.data_y) 
                                if x_min <= x <= x_max]
            
            if y_values_in_range:
                # 使用实际数据的Y范围，并添加适当的边距
                y_min_data = min(y_values_in_range)
                y_max_data = max(y_values_in_range)
                y_range = y_max_data - y_min_data
                
                # 添加10%的边距使图形更美观
                if y_range > 0:
                    y_margin = y_range * 0.1
                    y_min = y_min_data - y_margin
                    y_max = y_max_data + y_margin
                else:
                    # 如果Y值相同，使用固定边距
                    y_min = y_min_data - 10
                    y_max = y_max_data + 10
            else:
                # 如果没有数据，使用鼠标选择的范围
                y_min = min(y0, y1)
                y_max = max(y0, y1)
            
            # 应用新的范围
            self.x_min = x_min
            self.x_max = x_max
            self.y_min = y_min
            self.y_max = y_max
            
            # 更新输入框
            self.x_min_input.setText(f"{self.x_min:.2f}")
            self.x_max_input.setText(f"{self.x_max:.2f}")
            self.y_min_input.setText(f"{self.y_min:.2f}")
            self.y_max_input.setText(f"{self.y_max:.2f}")
            
            # 更新图形
            self.update_plot()
            
            self.press_event = None
    
    def draw_zoom_rect(self, event):
        """绘制缩放选择矩形"""
        if self.zoom_start is None or event.xdata is None or event.ydata is None:
            return
        
        x0, y0 = self.zoom_start
        x1, y1 = event.xdata, event.ydata
        
        # 清除旧矩形
        if self.zoom_rect:
            self.zoom_rect.remove()
        
        # 绘制新矩形
        width = x1 - x0
        height = y1 - y0
        
        self.zoom_rect = mpatches.Rectangle(
            (x0, y0), width, height,
            fill=False,
            edgecolor='red',
            linewidth=2,
            linestyle='--',
            alpha=0.7
        )
        self.ax.add_patch(self.zoom_rect)
        
        # 显示选择区域信息
        self.coord_label.setText(
            f"选择区域: X=[{min(x0,x1):.2f}, {max(x0,x1):.2f}], "
            f"Y=[{min(y0,y1):.2f}, {max(y0,y1):.2f}]"
        )
        
        self.canvas.draw_idle()
    
    def export_data(self):
        """导出分析数据"""
        if not self.data_x or not self.data_y:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出分析数据",
            f"Analysis_Data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "文本文件 (*.txt);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("# CS1237 ADC 数据分析\n")
                f.write(f"# 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# X轴范围: [{self.x_min:.4f}, {self.x_max:.4f}]\n")
                f.write(f"# Y轴范围: [{self.y_min:.2f}, {self.y_max:.2f}]\n")
                f.write(f"# 数据点数: {len(self.data_x)}\n")
                f.write("#" + "="*60 + "\n")
                f.write("# 时间(秒)\tADC值\n")
                
                for x, y in zip(self.data_x, self.data_y):
                    if self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max:
                        f.write(f"{x:.4f}\t{y}\n")
            
            QMessageBox.information(self, "成功", f"数据已导出到:\n{file_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败:\n{str(e)}")

# ==================== 主界面 ====================
class CS1237_GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CS1237 ADC 控制器 - 协议通信版")
        self.setGeometry(100, 100, 1200, 800)
        
        # 串口相关变量
        self.serial_port = None
        self.serial_thread = None
        self.is_connected = False
        self.is_continuous = False
        
        # 协议处理器
        self.protocol = ProtocolHandler()
        
        # 当前配置状态
        self.current_pga = 128.0
        self.current_sample_rate = "10 Hz"
        self.current_vref = 2.5
        
        # 绘图数据
        self.plot_data_x = deque(maxlen=1000)
        self.plot_data_y = deque(maxlen=1000)
        self.start_time = time.time()
        
        # 数据保存状态跟踪
        self.data_saved = True
        
        # 显示模式
        self.time_window = 600.0
        
        # 绘图优化参数
        self.last_draw_time = 0
        self.draw_interval = 0.05
        
        # Y轴范围平滑控制
        self.current_y_min = None
        self.current_y_max = None
        self.y_range_smooth_factor = 0.3
        
        # 异常值过滤参数
        self.enable_outlier_filter = True
        self.outlier_threshold = 3.5
        self.min_data_for_filter = 20
        self.recent_values = deque(maxlen=100)
        self.outlier_count = 0
        
        # 线程安全锁
        self.data_lock = threading.Lock()
        
        self.init_ui()
        self.refresh_ports()
        
    def init_ui(self):
        """初始化界面"""
        # 创建中心窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局：水平分割（左侧控制区 + 右侧显示区）
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # ==================== 左侧控制面板 ====================
        left_panel = QWidget()
        left_panel.setMaximumWidth(350)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        
        # 1. 串口参数组
        port_group = QGroupBox("串口参数")
        port_layout = QGridLayout()
        port_layout.setSpacing(8)
        
        port_layout.addWidget(QLabel("串口选择:"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumHeight(25)
        port_layout.addWidget(self.port_combo, 0, 1)
        
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setMaximumWidth(60)
        self.refresh_btn.clicked.connect(self.refresh_ports)
        port_layout.addWidget(self.refresh_btn, 0, 2)
        
        port_layout.addWidget(QLabel("波特率:"), 1, 0)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "115200", "57600", "38400"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setMinimumHeight(25)
        port_layout.addWidget(self.baud_combo, 1, 1, 1, 2)
        
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setMinimumHeight(35)
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.connect_btn.clicked.connect(self.toggle_connection)
        port_layout.addWidget(self.connect_btn, 2, 0, 1, 3)
        
        port_group.setLayout(port_layout)
        left_layout.addWidget(port_group)
        
        # 2. 数据操作组
        data_group = QGroupBox("数据操作")
        data_layout = QVBoxLayout()
        data_layout.setSpacing(8)
        
        self.ping_btn = QPushButton("Ping测试")
        self.ping_btn.setMinimumHeight(32)
        self.ping_btn.clicked.connect(self.ping)
        data_layout.addWidget(self.ping_btn)
        
        self.single_read_btn = QPushButton("单次读取")
        self.single_read_btn.setMinimumHeight(32)
        self.single_read_btn.clicked.connect(self.single_read)
        data_layout.addWidget(self.single_read_btn)
        
        self.continuous_btn = QPushButton("开始连续读取")
        self.continuous_btn.setMinimumHeight(32)
        self.continuous_btn.clicked.connect(self.toggle_continuous)
        data_layout.addWidget(self.continuous_btn)
        
        self.status_btn = QPushButton("查询状态")
        self.status_btn.setMinimumHeight(32)
        self.status_btn.clicked.connect(self.get_status)
        data_layout.addWidget(self.status_btn)
        
        data_group.setLayout(data_layout)
        left_layout.addWidget(data_group)
        
        # 3. 配置参数组
        config_group = QGroupBox("配置参数")
        config_layout = QGridLayout()
        config_layout.setSpacing(8)
        
        config_layout.addWidget(QLabel("PGA增益:"), 0, 0)
        self.pga_combo = QComboBox()
        self.pga_combo.addItems(["1", "2", "64", "128"])
        self.pga_combo.setCurrentText("128")
        self.pga_combo.setMinimumHeight(25)
        config_layout.addWidget(self.pga_combo, 0, 1)
        
        self.set_pga_btn = QPushButton("设置")
        self.set_pga_btn.setMaximumWidth(60)
        self.set_pga_btn.clicked.connect(self.set_pga)
        config_layout.addWidget(self.set_pga_btn, 0, 2)
        
        config_layout.addWidget(QLabel("采样率:"), 1, 0)
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(["10 Hz", "40 Hz", "640 Hz", "1280 Hz"])
        self.sample_rate_combo.setCurrentText("10 Hz")
        self.sample_rate_combo.setMinimumHeight(25)
        config_layout.addWidget(self.sample_rate_combo, 1, 1)
        
        self.set_rate_btn = QPushButton("设置")
        self.set_rate_btn.setMaximumWidth(60)
        self.set_rate_btn.clicked.connect(self.set_sample_rate)
        config_layout.addWidget(self.set_rate_btn, 1, 2)
        
        config_layout.addWidget(QLabel("参考电压:"), 2, 0)
        self.vref_combo = QComboBox()
        self.vref_combo.addItems(["2.5V", "3.3V", "5.0V"])
        self.vref_combo.setCurrentText("2.5V")
        self.vref_combo.setMinimumHeight(25)
        config_layout.addWidget(self.vref_combo, 2, 1)
        
        self.set_vref_btn = QPushButton("设置")
        self.set_vref_btn.setMaximumWidth(60)
        self.set_vref_btn.clicked.connect(self.set_vref)
        config_layout.addWidget(self.set_vref_btn, 2, 2)
        
        config_group.setLayout(config_layout)
        left_layout.addWidget(config_group)
        
        # 添加弹簧，将控件推到顶部
        left_layout.addStretch()
        
        # 数据分析按钮
        analyze_btn = QPushButton("📊 数据分析")
        analyze_btn.setMinimumHeight(35)
        analyze_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        analyze_btn.clicked.connect(self.open_analysis_window)
        left_layout.addWidget(analyze_btn)
        
        # 保存数据按钮
        save_data_btn = QPushButton("💾 保存数据")
        save_data_btn.setMinimumHeight(35)
        save_data_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
        """)
        save_data_btn.clicked.connect(self.save_data_manual)
        left_layout.addWidget(save_data_btn)
        
        # 清除输出按钮（底部）
        clear_output_btn = QPushButton("清除输出")
        clear_output_btn.setMinimumHeight(30)
        clear_output_btn.clicked.connect(self.clear_output)
        left_layout.addWidget(clear_output_btn)
        
        main_layout.addWidget(left_panel)
        
        # ==================== 右侧显示区域 ====================
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(5)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 上部：数据输出区域
        output_group = QGroupBox("数据输出")
        output_layout = QVBoxLayout()
        output_layout.setContentsMargins(5, 5, 5, 5)
        output_layout.setSpacing(2)
        
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setMinimumHeight(120)
        self.text_area.setMaximumHeight(180)
        font = QFont("Consolas", 9)
        self.text_area.setFont(font)
        output_layout.addWidget(self.text_area)
        
        output_group.setLayout(output_layout)
        output_group.setMinimumHeight(150)
        output_group.setMaximumHeight(220)

        # --- 右上区域：数据输出（左） + 最近20s小图（右） ---
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.setContentsMargins(0, 0, 0, 0)

        # 将 output_group 放入左侧
        top_row.addWidget(output_group, stretch=1)

        # 右侧小图
        self.small_fig = Figure(figsize=(6, 3.5), dpi=100)
        self.small_ax = self.small_fig.add_subplot(111)
        self.small_ax.set_title('最近 20s', fontsize=11)
        self.small_ax.set_xlabel('秒', fontsize=10)
        self.small_ax.set_ylabel('ADC', fontsize=10)
        self.small_ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.6)
        self.small_ax.set_facecolor('#ffffff')
        self.small_line, = self.small_ax.plot([], [], 'r-', linewidth=1.5, antialiased=True)
        self.small_canvas = FigureCanvas(self.small_fig)
        self.small_canvas.setMinimumHeight(150)
        self.small_canvas.setMaximumHeight(220)
        top_row.addWidget(self.small_canvas, stretch=2)

        right_layout.addLayout(top_row)

        # 下部：实时波形图
        plot_group = QGroupBox("实时波形图")
        plot_layout = QHBoxLayout()
        plot_layout.setContentsMargins(5, 2, 5, 5)

        # 创建matplotlib主图形
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel('时间 (秒)', fontsize=11)
        self.ax.set_ylabel('ADC 值', fontsize=11)
        self.ax.set_title('实时数据', fontsize=12, fontweight='bold')
        self.ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.8)
        self.ax.grid(True, which='minor', alpha=0.1, linestyle=':', linewidth=0.5)
        self.ax.set_facecolor('#f8f9fa')
        self.line, = self.ax.plot([], [], 'b-', linewidth=1.8, antialiased=True)
        self.canvas = FigureCanvas(self.fig)

        plot_layout.addWidget(self.canvas, stretch=1)
        plot_group.setLayout(plot_layout)
        right_layout.addWidget(plot_group, stretch=1)

        main_layout.addWidget(right_panel, stretch=1)
        
        # 状态栏
        self.statusBar().showMessage("就绪 - 请选择串口并连接")
        
    def refresh_ports(self):
        """刷新可用的串口列表"""
        self.port_combo.clear()
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo.addItems(ports)
        
    def toggle_connection(self):
        """连接/断开串口"""
        if not self.is_connected:
            self.connect_serial()
        else:
            self.disconnect_serial()
            
    def connect_serial(self):
        """连接串口"""
        try:
            port = self.port_combo.currentText()
            baud = int(self.baud_combo.currentText())
            
            if not port:
                QMessageBox.warning(self, "错误", "请选择串口")
                return
                
            self.serial_port = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # 等待Arduino重启
            
            # 清空可能残留的数据
            if self.serial_port.in_waiting > 0:
                self.serial_port.reset_input_buffer()
            
            self.is_connected = True
            self.connect_btn.setText("断开连接")
            self.connect_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f44336;
                    color: white;
                    font-weight: bold;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #da190b;
                }
            """)
            self.statusBar().showMessage(f"已连接: {port} @ {baud} baud")
            
            # 启动串口读取线程
            self.serial_thread = SerialThread(self.serial_port)
            self.serial_thread.data_received.connect(self.on_data_received)
            self.serial_thread.error_occurred.connect(self.on_error)
            self.serial_thread.start()
            
            # 发送ping测试连接
            self.ping()
            
        except Exception as e:
            QMessageBox.critical(self, "连接错误", f"无法连接串口: {str(e)}")
            
    def disconnect_serial(self):
        """断开串口连接"""
        # 停止连续读取
        if self.is_continuous:
            self.send_command(Command.CMD_CONTINUOUS_STOP)
            self.is_continuous = False
            self.continuous_btn.setText("开始连续读取")
        
        # 停止串口线程
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread.wait()
            self.serial_thread = None
            
        # 关闭串口
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            
        self.is_connected = False
        self.connect_btn.setText("连接")
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.statusBar().showMessage("已断开连接")
        
    def send_command(self, command, data=b''):
        """发送协议命令"""
        if not self.is_connected or not self.serial_port:
            return False
        
        try:
            frame = self.protocol.build_frame(command, data)
            self.serial_port.write(frame)
            return True
        except Exception as e:
            self.log_message(f"发送命令错误: {str(e)}\n")
            return False
            
    def on_data_received(self, raw_data):
        """处理接收到的串口数据"""
        try:
            # 使用协议处理器解析数据
            frames_parsed = self.protocol.process_received_data(
                raw_data, 
                self.handle_protocol_frame
            )
            
            # 如果没有解析到帧，可能是文本信息（用于调试）
            if frames_parsed == 0 and len(raw_data) > 0:
                try:
                    text = raw_data.decode('utf-8', errors='ignore').strip()
                    if text and self.should_display_line(text):
                        self.log_message(text + "\n")
                except:
                    pass
                    
        except Exception as e:
            self.log_message(f"数据处理错误: {str(e)}\n")
    
    def handle_protocol_frame(self, command, data):
        """处理协议帧"""
        try:
            if command == Command.CMD_ADC_DATA:
                self.handle_adc_data(data)
            elif command == Command.CMD_GET_STATUS:
                self.handle_status_data(data)
            elif command == Command.CMD_ACK:
                self.handle_ack(data)
            elif command == Command.CMD_ERROR:
                self.handle_error(data)
            else:
                self.log_message(f"未处理的命令: {command}, 数据: {data.hex()}\n")
        except Exception as e:
            self.log_message(f"处理协议帧错误: {str(e)}\n")
    
    def handle_adc_data(self, data):
        """处理ADC数据帧"""
        if len(data) < 8:
            return
            
        try:
            # 解析ADC值 (4字节有符号整数)
            adc_value = struct.unpack('>i', data[0:4])[0]
            
            # 解析电压值 (4字节浮点数)
            voltage = struct.unpack('>f', data[4:8])[0]
            
            current_time = time.time() - self.start_time
            
            # 更新数据缓冲区
            with self.data_lock:
                self.recent_values.append(adc_value)
                self.plot_data_x.append(current_time)
                self.plot_data_y.append(adc_value)
            
            # 标记数据未保存
            self.data_saved = False
            
            # 限制绘图频率
            now = time.time()
            if now - self.last_draw_time >= self.draw_interval:
                self.update_plot()
                self.last_draw_time = now
                
            # 在文本区域显示数据
            self.log_message(f"ADC: {adc_value:8d} | 电压: {voltage:10.6f} V\n")
            
        except Exception as e:
            self.log_message(f"解析ADC数据错误: {str(e)}\n")
    
    def handle_status_data(self, data):
        """处理状态数据帧"""
        if len(data) < 9:
            return
            
        try:
            # 解析PGA增益 (4字节浮点数)
            pga = struct.unpack('>f', data[0:4])[0]
            
            # 解析采样率 (1字节)
            sample_rate = data[4]
            
            # 解析参考电压 (4字节浮点数)
            vref = struct.unpack('>f', data[5:9])[0]
            
            # 更新当前配置
            self.current_pga = pga
            rate_text = ["10 Hz", "40 Hz", "640 Hz", "1280 Hz"][sample_rate]
            self.current_sample_rate = rate_text
            self.current_vref = vref
            
            # 更新UI显示
            self.log_message(f"状态: PGA={pga}, 采样率={rate_text}, Vref={vref}V\n")
            
        except Exception as e:
            self.log_message(f"解析状态数据错误: {str(e)}\n")
    
    def handle_ack(self, data):
        """处理应答帧"""
        success = data[0] if data else 0
        if success:
            self.log_message("✅ 命令执行成功\n")
        else:
            self.log_message("❌ 命令执行失败\n")
    
    def handle_error(self, data):
        """处理错误帧"""
        error_code = data[0] if data else 0
        error_messages = {
            0x01: "校验和错误",
            0x02: "未知命令",
            0x03: "ADC读取错误",
            0x04: "数据长度错误"
        }
        message = error_messages.get(error_code, f"未知错误 (代码: {error_code:02X})")
        self.log_message(f"❌ 错误: {message}\n")
    
    def should_display_line(self, line):
        """判断是否应该显示该行信息（用于调试信息过滤）"""
        filter_keywords = [
            "CS1237 ADC - Enhanced Mode",
            "Commands:",
            "Send 's' to stop",
            "Configuration mode",
            "Available commands:",
            "Data not ready"
        ]
        
        for keyword in filter_keywords:
            if keyword in line:
                return False
        
        return True
        
    def log_message(self, message):
        """在文本区域显示消息"""
        self.text_area.append(message.rstrip())
        self.text_area.verticalScrollBar().setValue(
            self.text_area.verticalScrollBar().maximum()
        )
        
    def clear_output(self):
        """清除输出区域"""
        self.text_area.clear()
    
    def ping(self):
        """发送ping命令"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command(Command.CMD_PING)
        
    def single_read(self):
        """单次读取数据"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command(Command.CMD_SINGLE_READ)
        
    def toggle_continuous(self):
        """切换连续读取模式"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        if not self.is_continuous:
            # 开始新的采集前检查
            if len(self.plot_data_x) > 0 and not self.data_saved:
                self.prompt_save_data()
            
            self.clear_plot()
            
            if self.send_command(Command.CMD_CONTINUOUS_START):
                self.is_continuous = True
                self.continuous_btn.setText("停止连续读取")
                self.start_time = time.time()
                self.last_draw_time = 0
                self.data_saved = False
                self.log_message("✅ 开始连续采样\n")
        else:
            if self.send_command(Command.CMD_CONTINUOUS_STOP):
                self.is_continuous = False
                self.continuous_btn.setText("开始连续读取")
                self.log_message("⏸️ 已停止采集\n")
    
    def prompt_save_data(self):
        """开始新采集前提示保存旧数据（仅在数据未保存时调用）"""
        if len(self.plot_data_x) == 0:
            return
        
        # 弹出对话框询问是否保存数据
        reply = QMessageBox.question(
            self, 
            '保存数据', 
            '检测到之前的采集数据未保存，是否保存？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # 用户选择保存
            self.export_data_to_txt()
    
    def save_data_manual(self):
        """手动保存数据按钮的处理函数"""
        if len(self.plot_data_x) == 0:
            QMessageBox.information(self, "提示", "当前没有数据可保存")
            return
        
        # 调用导出函数
        self.export_data_to_txt()
    
    def open_analysis_window(self):
        """打开数据分析窗口"""
        if len(self.plot_data_x) == 0:
            QMessageBox.information(self, "提示", "当前没有数据可分析\n请先采集数据")
            return
        
        # 创建并显示分析窗口
        try:
            analysis_window = DataAnalysisWindow(
                self.plot_data_x, 
                self.plot_data_y, 
                self
            )
            analysis_window.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开分析窗口失败:\n{str(e)}")
    
    def export_data_to_txt(self):
        """导出数据为TXT格式"""
        if len(self.plot_data_x) == 0:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return
        
        # 打开文件保存对话框
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存数据",
            f"ADC_Data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "文本文件 (*.txt);;所有文件 (*.*)"
        )
        
        if not file_path:
            # 用户取消了保存
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                # 写入文件头
                f.write("# CS1237 ADC 数据记录\n")
                f.write(f"# 记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# PGA增益: {self.current_pga}\n")
                f.write(f"# 采样率: {self.current_sample_rate}\n")
                f.write(f"# 参考电压: {self.current_vref}V\n")
                f.write(f"# 数据点数: {len(self.plot_data_x)}\n")
                f.write("#" + "="*60 + "\n")
                f.write("# 时间(秒)\tADC值\n")
                
                # 写入数据
                for t, v in zip(self.plot_data_x, self.plot_data_y):
                    f.write(f"{t:.3f}\t{v}\n")
            
            # 标记数据已保存
            self.data_saved = True
            
            QMessageBox.information(self, "成功", f"数据已保存到:\n{file_path}")
            self.log_message(f"✅ 数据已导出: {file_path}\n")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存文件失败:\n{str(e)}")
                
    def set_pga(self):
        """设置PGA增益"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        pga_map = {"1": 0, "2": 1, "64": 2, "128": 3}
        pga_value = self.pga_combo.currentText()
        
        if pga_value in pga_map:
            data = bytes([pga_map[pga_value]])
            self.send_command(Command.CMD_CONFIG_PGA, data)
            self.current_pga = float(pga_value)
            self.log_message(f"设置PGA: {pga_value}\n")
        else:
            QMessageBox.warning(self, "警告", "请选择有效的PGA值")
            
    def set_sample_rate(self):
        """设置采样率"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        rate_map = {"10 Hz": 0, "40 Hz": 1, "640 Hz": 2, "1280 Hz": 3}
        rate_value = self.sample_rate_combo.currentText()
        
        if rate_value in rate_map:
            data = bytes([rate_map[rate_value]])
            self.send_command(Command.CMD_CONFIG_RATE, data)
            self.current_sample_rate = rate_value
            self.log_message(f"设置采样率: {rate_value}\n")
        else:
            QMessageBox.warning(self, "警告", "请选择有效的采样率")
            
    def set_vref(self):
        """设置参考电压"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        vref_map = {"2.5V": 0, "3.3V": 1, "5.0V": 2}
        vref_value = self.vref_combo.currentText()
        
        if vref_value in vref_map:
            data = bytes([vref_map[vref_value]])
            self.send_command(Command.CMD_CONFIG_VREF, data)
            self.current_vref = float(vref_value.replace('V', ''))
            self.log_message(f"设置参考电压: {vref_value}\n")
        else:
            QMessageBox.warning(self, "警告", "请选择有效的参考电压")
            
    def get_status(self):
        """查询当前配置状态"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command(Command.CMD_GET_STATUS)
    
    def on_error(self, error_msg):
        """处理错误信息"""
        self.log_message(error_msg + "\n")
    
    def update_plot(self):
        """更新图形显示（600秒滚动窗口 = 10分钟）"""
        try:
            if len(self.plot_data_x) == 0 or len(self.plot_data_y) == 0:
                return
                
            x_data = list(self.plot_data_x)
            y_data = list(self.plot_data_y)
            
            # 只显示最近600秒的数据
            current_time = x_data[-1] if x_data else 0
            time_threshold = current_time - self.time_window
            
            display_indices = [i for i, t in enumerate(x_data) if t >= time_threshold]
            display_x = [x_data[i] for i in display_indices]
            display_y = [y_data[i] for i in display_indices]
            
            if not display_x or not display_y:
                self.line.set_data([], [])
                return
            
            # 数据抽样：当数据点过多时进行智能抽样，保持曲线流畅
            if len(display_x) > 500:
                step = len(display_x) // 500
                display_x = display_x[::step]
                display_y = display_y[::step]
            
            self.line.set_data(display_x, display_y)
            
            # X轴范围设置 - 智能调整
            x_min, x_max = min(display_x), max(display_x)
            x_range = x_max - x_min
            
            if x_range > 0:
                x_margin = max(0.5, x_range * 0.02)
                self.ax.set_xlim(x_min - x_margin, x_max + x_margin)
            else:
                self.ax.set_xlim(max(0, x_min - 1), x_min + self.time_window)
            
            # Y轴范围设置 - 平滑智能缩放
            y_min_raw, y_max_raw = min(display_y), max(display_y)
            y_range = y_max_raw - y_min_raw
            
            # 计算建议的Y轴范围
            if y_range > 0:
                if y_range < 10:
                    y_margin = 5
                elif y_range < 100:
                    y_margin = y_range * 0.20
                elif y_range < 1000:
                    y_margin = y_range * 0.15
                else:
                    y_margin = y_range * 0.10

                suggested_y_min = y_min_raw - y_margin
                suggested_y_max = y_max_raw + y_margin
            else:
                if abs(y_min_raw) < 10:
                    suggested_y_min = y_min_raw - 5
                    suggested_y_max = y_min_raw + 5
                elif abs(y_min_raw) < 1000:
                    margin = max(10, abs(y_min_raw) * 0.01)
                    suggested_y_min = y_min_raw - margin
                    suggested_y_max = y_min_raw + margin
                else:
                    margin = max(100, abs(y_min_raw) * 0.005)
                    suggested_y_min = y_min_raw - margin
                    suggested_y_max = y_min_raw + margin
            
            # 平滑过渡：确保Y轴不会剧烈跳动
            if self.current_y_min is None or self.current_y_max is None:
                # 首次设置
                self.current_y_min = suggested_y_min
                self.current_y_max = suggested_y_max
            else:
                # 平滑系数
                expand_alpha = 0.5  # 扩展时的平滑系数（较快响应）
                shrink_alpha = 0.1  # 收缩时的平滑系数（较慢响应，保持稳定）
                
                # Y轴下限调整
                if suggested_y_min < self.current_y_min:
                    # 向下扩展
                    self.current_y_min = self.current_y_min * (1 - expand_alpha) + suggested_y_min * expand_alpha
                else:
                    # 向上收缩（慢速）
                    self.current_y_min = self.current_y_min * (1 - shrink_alpha) + suggested_y_min * shrink_alpha
                
                # Y轴上限调整
                if suggested_y_max > self.current_y_max:
                    # 向上扩展
                    self.current_y_max = self.current_y_max * (1 - expand_alpha) + suggested_y_max * expand_alpha
                else:
                    # 向下收缩（慢速）
                    self.current_y_max = self.current_y_max * (1 - shrink_alpha) + suggested_y_max * shrink_alpha
            
            # 设置平滑后的Y轴范围
            self.ax.set_ylim(self.current_y_min, self.current_y_max)

            # 自动调整刻度
            self.auto_adjust_ticks(x_range, y_range, len(display_x))

            # 小图：显示最近20秒的数据
            try:
                small_time_threshold = current_time - 20.0
                small_indices = [i for i, t in enumerate(x_data) if t >= small_time_threshold]
                small_x = [x_data[i] for i in small_indices]
                small_y = [y_data[i] for i in small_indices]

                if small_x and small_y:
                    # 抽样以防数据过多
                    if len(small_x) > 200:
                        step2 = len(small_x) // 200
                        small_x = small_x[::step2]
                        small_y = small_y[::step2]

                    self.small_line.set_data(small_x, small_y)
                    # 设置小图 X/Y 范围
                    self.small_ax.set_xlim(small_time_threshold, current_time)
                    sy_min, sy_max = min(small_y), max(small_y)
                    srange = sy_max - sy_min
                    if srange == 0:
                        self.small_ax.set_ylim(sy_min - 5, sy_min + 5)
                    else:
                        smargin = max(1, srange * 0.1)
                        self.small_ax.set_ylim(sy_min - smargin, sy_max + smargin)
                else:
                    self.small_line.set_data([], [])

                # 重新绘制小画布
                self.small_canvas.draw_idle()
            except Exception:
                pass

            # 重新绘制主画布
            self.canvas.draw_idle()
        except Exception as e:
            # 捕获绘图时的异常，记录到输出区域，避免程序崩溃
            try:
                self.log_message(f"绘图错误: {str(e)}\n")
            except Exception:
                print(f"绘图错误: {e}")
            
    def auto_adjust_ticks(self, x_range, y_range, data_count):
        """根据数据范围和密度智能调整刻度间距"""
        
        # X轴刻度（时间轴）优化
        if x_range < 5:
            x_ticks = 5
            x_minor_divs = 2
        elif x_range < 10:
            x_ticks = 6
            x_minor_divs = 2
        elif x_range < 20:
            x_ticks = 8
            x_minor_divs = 4
        elif x_range < 60:
            x_ticks = 10
            x_minor_divs = 5
        else:
            x_ticks = 12
            x_minor_divs = 6
        
        self.ax.xaxis.set_major_locator(MaxNLocator(nbins=x_ticks, integer=False, prune='both'))
        self.ax.xaxis.set_minor_locator(AutoMinorLocator(x_minor_divs))
        
        # Y轴刻度（ADC值）优化
        if y_range == 0:
            y_ticks = 6
            y_minor_divs = 2
        elif y_range < 10:
            y_ticks = 6
            y_minor_divs = 2
        elif y_range < 50:
            y_ticks = 8
            y_minor_divs = 2
        elif y_range < 100:
            y_ticks = 8
            y_minor_divs = 4
        elif y_range < 500:
            y_ticks = 10
            y_minor_divs = 5
        elif y_range < 1000:
            y_ticks = 10
            y_minor_divs = 5
        elif y_range < 10000:
            y_ticks = 8
            y_minor_divs = 4
        else:
            y_ticks = 8
            y_minor_divs = 2
        
        # 使用智能定位器
        self.ax.yaxis.set_major_locator(MaxNLocator(nbins=y_ticks, integer=False, prune='both'))
        self.ax.yaxis.set_minor_locator(AutoMinorLocator(y_minor_divs))
        
        # 网格线优化
        if data_count > 300:
            major_alpha = 0.25
            minor_alpha = 0.08
        elif data_count > 100:
            major_alpha = 0.3
            minor_alpha = 0.1
        else:
            major_alpha = 0.35
            minor_alpha = 0.12
        
        self.ax.grid(True, which='major', alpha=major_alpha, linestyle='-', linewidth=0.8)
        self.ax.grid(True, which='minor', alpha=minor_alpha, linestyle=':', linewidth=0.5)
        
        # 刻度标签格式化
        y_max_abs = max(abs(self.ax.get_ylim()[0]), abs(self.ax.get_ylim()[1]))
        
        if y_max_abs > 1000000:
            # 超大数值：使用科学计数法
            formatter = ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((0, 0))
            self.ax.yaxis.set_major_formatter(formatter)
        elif y_max_abs > 10000:
            # 大数值：使用千位分隔符
            def format_with_commas(x, pos):
                return f'{int(x):,}'
            self.ax.yaxis.set_major_formatter(FuncFormatter(format_with_commas))
        else:
            # 普通数值：标准显示
            self.ax.yaxis.set_major_formatter(ScalarFormatter())
        
    def clear_plot(self):
        """清除图形数据"""
        with self.data_lock:
            self.plot_data_x.clear()
            self.plot_data_y.clear()
            self.recent_values.clear()
        
        self.line.set_data([], [])
        
        # 清除小图数据
        self.small_line.set_data([], [])
        
        # 重置Y轴平滑控制
        self.current_y_min = None
        self.current_y_max = None
        
        # 重置为默认视图
        self.ax.set_xlim(0, self.time_window)
        self.ax.set_ylim(-100, 100)
        
        # 重置网格
        self.ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.8)
        self.ax.grid(True, which='minor', alpha=0.1, linestyle=':', linewidth=0.5)
        
        # 重置刻度格式
        self.ax.yaxis.set_major_formatter(ScalarFormatter())
        
        # 清除异常值统计数据
        self.outlier_count = 0
        
        # 标记数据已保存（因为已清空）
        self.data_saved = True

        # 重绘画布
        self.canvas.draw()
        self.small_canvas.draw()
        
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 检查是否有未保存的数据
        if len(self.plot_data_x) > 0 and not self.data_saved:
            reply = QMessageBox.question(
                self,
                '保存数据',
                '检测到有未保存的采集数据，是否保存？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # 用户选择保存
                self.export_data_to_txt()
                # 如果用户在保存对话框中取消了，则不关闭窗口
                if not self.data_saved:
                    event.ignore()
                    return
            elif reply == QMessageBox.StandardButton.Cancel:
                # 用户取消关闭操作
                event.ignore()
                return
            # 如果选择No，则继续关闭
        
        # 断开串口连接
        if self.is_connected:
            self.disconnect_serial()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = CS1237_GUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()