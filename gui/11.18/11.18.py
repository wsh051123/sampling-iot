import sys
import time
import re
from collections import deque
from datetime import datetime
import threading

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QTextEdit, QGroupBox, QGridLayout, QMessageBox,
                             QFileDialog, QLineEdit, QDialog)
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
import csv
import os
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class SerialThread(QThread):
    """串口读取线程 - 健壮地处理混合数据流 (文本 + 二进制帧) - 积极文本处理版"""
    data_received = pyqtSignal(str)
    frame_received = pyqtSignal(int, bytes)
    error_occurred = pyqtSignal(str)

    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True
        self.buffer = bytearray()
        
        self.FRAME_HEAD = b'\xaa\x55'
        self.FRAME_TAIL = b'\x0d\x0a'

    def run(self):
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                # 检查是否有数据
                if self.serial_port.in_waiting > 0:
                    new_data = self.serial_port.read(self.serial_port.in_waiting)
                    self.buffer.extend(new_data)
                else:
                    # 如果缓冲区也为空，则短暂休眠
                    if not self.buffer:
                        time.sleep(0.01)
                        continue
                
                # 无论是否读到新数据，都尝试处理缓冲区
                
                # 1. 优先处理所有完整的二进制帧
                processed_frame = self.parse_one_frame()
                if processed_frame:
                    # 如果处理了一个帧，立即再次循环，优先处理下一个可能的帧
                    continue

                # 2. 如果没有找到完整帧，检查是否有可能是不完整的帧正在等待更多数据
                # 如果 buffer 以帧头开始，可能是不完整的帧，保留它等待更多数据
                if self.buffer.startswith(self.FRAME_HEAD):
                    # 可能是不完整的帧，保留buffer，不处理为文本
                    # 但如果buffer太大（超过合理帧大小），说明可能不是有效帧
                    if len(self.buffer) > 256:  # 假设最大帧长度不超过256字节
                        # buffer太大，可能帧头是误判，删除第一个字节继续
                        text_part = self.buffer[:1]
                        self.emit_text(text_part)
                        self.buffer = self.buffer[1:]
                    # 否则，什么都不做，等待更多数据到来
                    continue
                
                # 3. 如果 buffer 不以帧头开始，且有换行符，则作为文本处理
                if b'\n' in self.buffer:
                    self.emit_text(self.buffer)
                    self.buffer.clear()

            except serial.SerialException as e:
                if self.running:
                    self.error_occurred.emit(f"串口错误: {str(e)}")
                break
            except Exception as e:
                if self.running:
                    print(f"线程中发生未知错误: {e}")
                pass

    def parse_one_frame(self) -> bool:
        """
        尝试从缓冲区解析一个完整的二进制帧。
        如果成功，则处理帧头前的文本、发射帧信号、从缓冲区移除数据，并返回 True。
        如果不成功（没有帧或帧不完整），则返回 False。
        """
        head_idx = self.buffer.find(self.FRAME_HEAD)
        
        if head_idx == -1:
            return False # 没有帧头
        
        if len(self.buffer) < head_idx + 3:
            return False # 数据不足以读取长度

        payload_len = self.buffer[head_idx + 2]
        if payload_len < 1:
            # 长度字段至少应包含命令字
            text_part = self.buffer[:head_idx + 1]
            self.emit_text(text_part)
            self.buffer = self.buffer[head_idx + 1:]
            return True

        frame_len = 2 + 1 + payload_len + 1 + 2

        if len(self.buffer) < head_idx + frame_len:
            return False # 帧不完整
        
        frame = self.buffer[head_idx : head_idx + frame_len]
        
        # 验证帧尾和校验和
        if not frame.endswith(self.FRAME_TAIL):
            print(f"DEBUG - 帧尾不匹配: {frame[-2:].hex()} != {self.FRAME_TAIL.hex()}")
            # 帧尾错误，删除当前帧头第一个字节，继续查找
            text_part = self.buffer[:head_idx + 1]
            self.emit_text(text_part)
            self.buffer = self.buffer[head_idx + 1:]
            return True
        
        if not self.verify_checksum(frame):
            print(f"DEBUG - 校验和失败: frame={frame.hex()}")
            # 校验失败，删除当前帧头第一个字节，继续查找
            text_part = self.buffer[:head_idx + 1]
            self.emit_text(text_part)
            self.buffer = self.buffer[head_idx + 1:]
            return True
        
        # 帧验证成功
        # 处理帧头前的文本
        text_part_before_frame = self.buffer[:head_idx]
        if text_part_before_frame:
            self.emit_text(text_part_before_frame)

        cmd = frame[3]
        data_len = max(0, payload_len - 1)
        data = frame[4 : 4 + data_len]
        data_bytes = bytes(data)
        
        print(f"DEBUG - 成功解析帧: cmd=0x{cmd:02X}, data_len={data_len}, data={data.hex()}")
        self.frame_received.emit(cmd, data_bytes)
        
        self.buffer = self.buffer[head_idx + frame_len:]
        return True # 成功处理了一个帧

    def verify_checksum(self, frame):
        try:
            checksum_byte = frame[-3]
            calculated_checksum = 0
            for byte in frame[2:-3]:
                calculated_checksum ^= byte
            return checksum_byte == calculated_checksum
        except IndexError:
            return False

    def emit_text(self, byte_data):
        try:
            # 使用 errors='replace' 可以在遇到无效字节时用'?'替代，而不是忽略
            text = byte_data.decode('utf-8', errors='replace').strip('\r\n\x00')
            if text:
                self.data_received.emit(text)
        except Exception:
            pass
            
    # 这个函数不再需要，因为处理逻辑已移入run循环
    # def process_remaining_text(self):
    #     pass

    def stop(self):
        self.running = False
    
    def parse_frames(self):
        """解析缓冲区中的帧"""
        while len(self.buffer) >= 8:  # 最小帧长度
            # 查找帧头
            head_idx = self.buffer.find(self.FRAME_HEAD)
            if head_idx == -1:
                # 没找到帧头，只保留最后几个字节
                if len(self.buffer) > 100:
                    self.buffer = self.buffer[-10:]
                break
            
            # 删除帧头前的垃圾数据
            if head_idx > 0:
                self.buffer = self.buffer[head_idx:]
            
            # 检查是否有足够的数据
            if len(self.buffer) < 8:
                break
            
            # 提取长度字节
            data_len = self.buffer[2]
            frame_len = 2 + 1 + 1 + data_len + 1 + 2  # 帧头+长度+命令+数据+校验+帧尾
            
            if len(self.buffer) < frame_len:
                # 数据不完整，等待更多数据
                break
            
            # 提取完整帧
            frame = self.buffer[:frame_len]
            
            # 验证帧尾
            if frame[-2:] != self.FRAME_TAIL:
                # 帧尾错误，删除当前帧头，继续查找
                self.buffer = self.buffer[2:]
                continue
            
            # 验证校验和
            checksum = 0
            for i in range(2, frame_len - 3):
                checksum ^= frame[i]
            
            if checksum != frame[frame_len - 3]:
                # 校验失败，删除当前帧头，继续查找
                self.buffer = self.buffer[2:]
                continue
            
            # 帧解析成功
            cmd = frame[3]
            data = frame[4:4+data_len]
            
            # 发送信号
            self.frame_received.emit(cmd, bytes(data))
            
            # 删除已处理的帧
            self.buffer = self.buffer[frame_len:]
    
    


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
            # 同时导出 CSV 格式
            try:
                base, _ = os.path.splitext(file_path)
                csv_path = base + '.csv'
                with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                    writer = csv.writer(cf)
                    writer.writerow(['time_s', 'adc'])
                    for x, y in zip(self.data_x, self.data_y):
                        if self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max:
                            writer.writerow([f"{x:.4f}", y])
                QMessageBox.information(self, "成功", f"数据已导出到:\n{file_path}\n{csv_path}")
            except Exception as e:
                QMessageBox.information(self, "成功", f"数据已导出到:\n{file_path}\n(同时导出 CSV 失败: {e})")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败:\n{str(e)}")


class CommandSequencer(QThread):
    """
    用于处理与Arduino多步交互的命令序列执行器。
    (累积响应版，解决文本碎片问题)
    """
    sequence_finished = pyqtSignal(bool, str)

    def __init__(self, parent_gui, sequence):
        super().__init__()
        self.gui = parent_gui
        self.sequence = sequence
        self.response_buffer = ""
        self.response_event = threading.Event()
        self.running = True
        # 使用一个锁来保护response_buffer，防止竞态条件
        self.buffer_lock = threading.Lock()

    def run(self):
        if not self.gui.serial_thread:
            self.sequence_finished.emit(False, "串口线程未运行")
            return
            
        # 安全地连接信号
        try:
            self.gui.serial_thread.data_received.connect(self.on_response_received)
        except Exception as e:
            self.sequence_finished.emit(False, f"连接信号失败: {e}")
            return
        
        # 清空初始缓冲区
        with self.buffer_lock:
            self.response_buffer = ""
        
        success = True
        error_message = ""

        for step_type, value in self.sequence:
            if not self.running:
                success = False
                error_message = "操作被取消"
                break
                
            if step_type == 'send':
                # 在发送命令前，短暂等待以收集之前的响应
                time.sleep(0.3)
                if not self.gui.send_command(value, delay=0.3):
                    success = False
                    error_message = "命令发送失败"
                    break
            
            elif step_type == 'wait_for':
                keywords = value
                if not isinstance(keywords, (list, tuple, set)):
                    keywords = [keywords]
                keywords = [kw for kw in keywords if isinstance(kw, str) and kw]
                if not keywords:
                    continue

                hint = " / ".join(keywords[:3])
                self.gui.log_message(f"🔍 等待关键字: {hint} ...\n", category="progress")

                start_time = time.time()
                found = False
                hit_keyword = None

                while time.time() - start_time < 5.0:
                    with self.buffer_lock:
                        buffer_snapshot = self.response_buffer
                    for kw in keywords:
                        if kw in buffer_snapshot:
                            found = True
                            hit_keyword = kw
                            self.gui.log_message(
                                f"✅ 找到关键字: '{kw}' (缓冲区: {len(buffer_snapshot)} 字符)\n",
                                category="status",
                            )
                            with self.buffer_lock:
                                self.response_buffer = buffer_snapshot.split(kw, 1)[1]
                            break
                    if found:
                        break
                    time.sleep(0.05)

                if not found:
                    success = False
                    with self.buffer_lock:
                        buffer_preview = self.response_buffer[-300:] if len(self.response_buffer) > 300 else self.response_buffer
                    error_message = (
                        f"等待 {keywords} 超时. 收到: '{buffer_preview}'"
                    )
                    self.gui.log_message(f"❌ {error_message}\n", category="error")
                    break
        
        # 安全地断开信号
        try:
            self.gui.serial_thread.data_received.disconnect(self.on_response_received)
        except Exception:
            pass

        self.sequence_finished.emit(success, "成功" if success else error_message)

    def on_response_received(self, text):
        # 使用锁来安全地追加数据
        with self.buffer_lock:
            self.response_buffer += text

    def stop(self):
        self.running = False


class CS1237_GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CS1237 ADC 控制器")
        self.setGeometry(100, 100, 1200, 800)
        
        # 串口相关变量
        self.serial_port = None
        self.serial_thread = None
        self.is_connected = False
        self.is_continuous = False
        self.menu_text_warning_shown = False
        self.show_adc_only = True
        # 仅在文本框显示必要信息（ADC、状态、成功/失败）
        self.allowed_output_categories = {
            "adc",
            "status",
            "result",
            "error",
            "warning",
            "general",
        }
        
        # 当前配置状态
        self.current_pga = 128.0
        self.current_sample_rate = "10 Hz"
        self.current_channel_code = 0
        self.channel_labels = {
            0: "通道A（差分）",
            1: "保留",
            2: "温度传感器",
            3: "内短模式"
        }
        self.vref = 5.0  # 与固件保持一致，默认为供电电压
        self.power_down = False
        
        # 绘图数据（保留所有接收点以便后续导出/分析）
        # 注意：不限制长度会随运行时间占用更多内存，已在绘图时保留抽样以控制渲染性能
        self.plot_data_x = deque()
        self.plot_data_y = deque()

        self.start_time = time.time()

        # 数据保存状态跟踪
        self.data_saved = True  # 标记当前数据是否已保存

        # 显示模式（固定为600秒滚动窗口 = 10分钟）
        self.time_window = 600.0

        # 绘图优化参数
        self.last_draw_time = 0
        self.draw_interval = 0.05  # 最小绘图间隔（秒），避免过于频繁更新

        # 显示模式：累计显示（从0开始并保留所有点）或滑动窗口
        # True = 累计（保留所有点、X轴从0开始）；False = 滑动窗口（默认旧行为）
        self.cumulative_mode = True

        # Y轴范围平滑控制（防止剧烈跳动）
        self.current_y_min = None  # 当前Y轴最小值
        self.current_y_max = None  # 当前Y轴最大值
        self.y_range_smooth_factor = 0.3  # 平滑因子（0-1），越小越平滑
        
        # 异常值过滤参数（简化版）
        self.enable_outlier_filter = True  # 是否启用异常值过滤
        self.outlier_threshold = 3.5  # MAD异常值阈值（修正Z分数）
        self.min_data_for_filter = 20  # 至少需要20个数据点才开始统计过滤
        self.recent_values = deque(maxlen=100)  # 保存最近100个值用于计算统计特征（增加窗口大小以提高稳定性）
        self.outlier_count = 0  # 被过滤的异常值计数
        
        # 单点脉冲检测缓冲区（简化为滑动窗口）
        self.spike_buffer = deque(maxlen=5)  # 存储 (time, value)，用于3点脉冲检测
        
        # 线程安全锁
        self.data_lock = threading.Lock()
        # 处理缓冲与前后文判定所需的结构（用于基于前后点的异常检测/替换）
        self.processing_buffer = deque()
        self.buffer_lock = threading.Lock()
        self.buffered_points = deque()
        self.total_received = 0
        # lookahead 表示在 buffered_points 中需要多少个后向点才能判定最左侧点，
        # 我们在新逻辑中使用前后各4个点判断，所以设置为4
        self.lookahead = 4
        # context_window（保留兼容性）表示用于前向上下文的长度，默认取8（前4+后4）
        self.context_window = 8
        # 用于初始阶段等待的最小点数（在积累到该数量前不绘图）
        # 已调整为较小的值以便更早进行基于前后文的异常检测
        self.min_points_before_plot = 8

        self.init_ui()
        self.refresh_ports()
        # 用于检测用户是否手动调整了视图；只有在上次自动设置的范围未被用户改动时才覆盖轴范围
        self._last_auto_xlim = None
        self._last_auto_ylim = None
        
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

        config_layout.addWidget(QLabel("输入通道:"), 2, 0)
        self.channel_combo = QComboBox()
        self.channel_combo.addItems([
            "通道A（差分）",
            "保留",
            "温度传感器",
            "内短模式"
        ])
        self.channel_combo.setCurrentIndex(0)
        self.channel_combo.setMinimumHeight(25)
        config_layout.addWidget(self.channel_combo, 2, 1)

        self.set_channel_btn = QPushButton("设置")
        self.set_channel_btn.setMaximumWidth(60)
        self.set_channel_btn.clicked.connect(self.set_channel)
        config_layout.addWidget(self.set_channel_btn, 2, 2)

        config_layout.addWidget(QLabel("电源模式:"), 3, 0)
        power_layout = QHBoxLayout()
        power_layout.setSpacing(6)
        self.power_down_btn = QPushButton("进入省电")
        self.power_down_btn.setMinimumHeight(28)
        self.power_down_btn.clicked.connect(self.enter_power_down)
        power_layout.addWidget(self.power_down_btn)
        self.power_up_btn = QPushButton("退出省电")
        self.power_up_btn.setMinimumHeight(28)
        self.power_up_btn.clicked.connect(self.exit_power_down)
        power_layout.addWidget(self.power_up_btn)
        config_layout.addLayout(power_layout, 3, 1, 1, 2)
        
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
        # 创建右上横向布局，将数据输出与小图并列
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.setContentsMargins(0, 0, 0, 0)

        # 将 output_group 放入左侧
        top_row.addWidget(output_group, stretch=1)

        # 右侧小图（增加尺寸以便更清晰）
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
        # 把小画布放到右上角（占比更大）
        top_row.addWidget(self.small_canvas, stretch=2)

        # 将 top_row 加入右侧布局
        right_layout.addLayout(top_row)

        # 下部：实时波形图（占据主要空间） - 完全占用下面的区域
        plot_group = QGroupBox("实时波形图")
        plot_layout = QHBoxLayout()
        plot_layout.setContentsMargins(5, 2, 5, 5)

        # 创建matplotlib主图形（大图）
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

        # 主绘图区放在下方并占据全部宽度
        plot_layout.addWidget(self.canvas, stretch=1)
        plot_group.setLayout(plot_layout)
        right_layout.addWidget(plot_group, stretch=1)

        main_layout.addWidget(right_panel, stretch=1)
        
        # 6. 状态栏
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
            self.serial_thread.frame_received.connect(self.on_frame_received)  # 新增：帧接收
            self.serial_thread.error_occurred.connect(self.on_error)
            self.serial_thread.start()
            
        except Exception as e:
            QMessageBox.critical(self, "连接错误", f"无法连接串口: {str(e)}")
            
    def disconnect_serial(self):
        """断开串口连接"""
        # 停止连续读取
        if self.is_continuous:
            self.send_command('s')
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
            # 断开前尽量刷新缓冲区剩余数据到绘图
            try:
                self._flush_processing_buffer(force=True)
                self.update_plot()
            except Exception:
                pass
            
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
        
    def send_command(self, command, delay=0.05):
        """发送命令到Arduino"""
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(command.encode())
                time.sleep(delay)
                return True
            except Exception as e:
                self.log_message(f"发送命令错误: {str(e)}\n", category="error")
                return False
        else:
            QMessageBox.warning(self, "警告", "串口未连接")
            return False
            
    def on_data_received(self, line):
        """处理接收到的串口文本数据"""
        # 调试：显示所有接收到的原始数据
        print(f"DEBUG - 接收到文本数据: {repr(line)}")
        
        # 🔧 关键修复：文本处理器现在只处理调试信息，不处理任何ADC数据
        # 所有ADC数据必须来自二进制帧
        
        # 拒绝任何包含"RAW ADC"或"Voltage"的行（这些应该来自二进制帧）
        if "RAW ADC" in line or "Voltage" in line:
            self.log_message(
                "⚠️ 警告：检测到文本格式ADC数据！这不应该出现。\n",
                category="warning",
                persist_status=True,
            )
            self.log_message(f"   疑似数据: {line}\n", category="warning")
            return
        
        # 拒绝包含二进制标记的数据
        if '\xaa' in line or '\x55' in line or any(ord(c) < 32 and c not in '\r\n\t' for c in line):
            # 包含二进制数据，忽略
            return

        if not self.menu_text_warning_shown:
            menu_keywords = ("Commands", "采样率", "快速设置", "Power down", "Configuration")
            if any(keyword in line for keyword in menu_keywords):
                self.log_message(
                    "⚠️ 检测到Arduino菜单文本，请确保固件在连续采样模式下只输出二进制帧。\n",
                    category="warning",
                    persist_status=True,
                )
                self.menu_text_warning_shown = True
        
        # 只显示纯文本调试信息
        if self.should_display_line(line):
            self.log_message(line + "\n", category="status")
        
        # ⚠️ 完全禁用 extract_and_plot_adc - 所有ADC数据必须来自二进制帧
        # self.extract_and_plot_adc(line)  # 已禁用
    
    def on_frame_received(self, cmd, data):
        """处理接收到的协议帧"""
        try:
            if cmd == 0x01:  # CMD_ADC_DATA
                self.handle_adc_frame(data)
            elif cmd == 0x03:  # CMD_ERROR
                self.handle_error_frame(data)
            elif cmd == 0x04:  # CMD_STATUS
                self.handle_status_frame(data)
            elif cmd == 0xB1:  # CMD_CONFIG_ACK
                self.handle_config_ack_frame(data)
            else:
                print(f"未知命令: 0x{cmd:02X}")
        except Exception as e:
            self.log_message(f"帧处理错误: {str(e)}\n", category="error")
    
    def handle_adc_frame(self, data):
        """处理ADC数据帧"""
        if len(data) != 4:
            print(f"⚠️ ADC帧长度错误: {len(data)} 字节 (应为4)")
            return
        
        signed_value = int.from_bytes(data, byteorder='big', signed=True)
        raw_hex = " ".join(f"{b:02X}" for b in data)
        print(f"DEBUG - ADC帧: [{raw_hex}] → 有符号值: {signed_value}")

        channel_code = getattr(self, 'current_channel_code', 0)
        channel_label = self.channel_labels.get(channel_code, f"CH{channel_code}")

        if channel_code == 2:  # 温度测量
            measurement_value = self.adc_to_temperature(signed_value)
            measurement_text = f"温度: {measurement_value:+.2f} ℃"
        else:
            measurement_value = self.adc_to_voltage(signed_value)
            measurement_text = f"电压: {measurement_value:+.8f} V"
        
        # 计算时间
        current_time = time.time() - self.start_time
        
        # 🔧 协议优势：直接使用已验证的数据，不需要复杂的异常检测
        # 因为Arduino端已经过滤了SPI错误
        
        # 添加到缓冲区（仍使用原有的处理流程进行平滑）
        try:
            with self.buffer_lock:
                self.buffered_points.append((current_time, signed_value))
                self.total_received += 1
        except Exception:
            self.buffered_points.append((current_time, signed_value))
            self.total_received += 1
        
        # 显示每个数据到日志框（保留原始帧和换算结果）
        text_line = (
            f"帧[{raw_hex}] → 通道 {channel_label} | ADC={signed_value} | {measurement_text} | 点数={self.total_received}"
        )
        self.log_message(text_line + "\n", category="adc")
        
        # 处理缓冲区数据
        if self.total_received >= getattr(self, 'min_points_before_plot', 50):
            try:
                while len(self.buffered_points) > self.lookahead:
                    t_candidate, v_candidate = self.buffered_points[0]
                    
                    # 使用前后文检测（保留原有逻辑）
                    prev_needed = 4
                    next_needed = 4
                    # 尝试从已写出的绘图数据中获取前向上下文；若不足则回退到 recent_values
                    prev_context = []
                    if prev_needed > 0:
                        if len(self.plot_data_y) >= prev_needed:
                            prev_context = list(self.plot_data_y)[-prev_needed:]
                        else:
                            # recent_values 保存最近的已输出值，作为回退来源
                            if len(self.recent_values) >= prev_needed:
                                prev_context = list(self.recent_values)[-prev_needed:]
                            else:
                                prev_context = list(self.recent_values)
                    
                    next_ctx_list = [v for (_, v) in list(self.buffered_points)[1:1 + next_needed]]
                    local_window = list(prev_context) + list(next_ctx_list)

                    # 如果候选值非常大，记录简短的上下文以便调试
                    try:
                        if abs(v_candidate) > 1000000:
                            self.log_message(
                                f"DEBUG_CONTEXT: candidate={v_candidate}, prev={prev_context}, next={next_ctx_list}, local_len={len(local_window)}\n",
                                category="debug",
                            )
                    except Exception:
                        pass
                    
                    # 检测异常（现在主要用于统计，因为Arduino已过滤SPI错误）
                    is_outlier_ctx = False
                    replacement_value = None
                    replacement_next = None
                    try:
                        is_outlier_ctx, replacement_value, replacement_next = self.is_outlier_in_context(
                            v_candidate, prev_context, next_ctx_list, local_window
                        )
                    except Exception:
                        is_outlier_ctx = False
                        replacement_value = None
                        replacement_next = None

                    # 如果返回了对下一个点的替换值，则直接修改 buffer 中的第二个元素
                    try:
                        if replacement_next is not None and len(self.buffered_points) > 1:
                            t_next = self.buffered_points[1][0]
                            old_next = self.buffered_points[1][1]
                            self.buffered_points[1] = (t_next, replacement_next)
                            try:
                                # 记录替换操作，便于排查
                                self.log_message(
                                    f"🔁 替换下一个缓冲点: 原值={old_next} -> 新值={replacement_next}",
                                    category="debug",
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass

                    if is_outlier_ctx:
                        self.outlier_count += 1
                        try:
                            if replacement_value is not None:
                                v_emit = int(replacement_value)
                                try:
                                    self.log_message(
                                        f"🔁 替换候选点: 原值={v_candidate} -> 新值={v_emit}",
                                        category="debug",
                                    )
                                except Exception:
                                    pass
                            elif len(local_window) >= 8:
                                mean_val = sum(local_window) / len(local_window)
                                v_emit = int(round(mean_val))
                            else:
                                if len(local_window) > 0:
                                    sorted_win = sorted(local_window)
                                    median = sorted_win[len(sorted_win) // 2]
                                    v_emit = int(median)
                                else:
                                    v_emit = int(v_candidate)
                        except Exception:
                            v_emit = int(v_candidate)
                    else:
                        v_emit = int(v_candidate)
                    
                    # 写入绘图数据
                    self.recent_values.append(v_emit)
                    self.plot_data_x.append(t_candidate)
                    self.plot_data_y.append(v_emit)
                    self.data_saved = False
                    
                    try:
                        self.buffered_points.popleft()
                    except Exception:
                        break
            except Exception:
                pass
        
        # 更新图形
        now = time.time()
        if now - self.last_draw_time >= self.draw_interval:
            self.update_plot()
            self.last_draw_time = now
    
    def handle_error_frame(self, data):
        """处理错误帧"""
        if len(data) < 1:
            return
        
        error_code = data[0]
        error_msgs = {
            0x01: "SPI读取失败",
            0x02: "数据无效",
            0x03: "超时",
            0x04: "测温模式需设置PGA=1"
        }
        msg = error_msgs.get(error_code, f"未知错误 (0x{error_code:02X})")
        self.log_message(f"⚠️ Arduino报告错误: {msg}\n", category="error")
    
    def handle_status_frame(self, data):
        """处理状态帧"""
        if len(data) < 5:
            return

        pga_code = data[0]
        rate_code = data[1]
        channel_code = data[2]

        remaining_bytes = data[3:]
        success_count = 0
        for b in remaining_bytes:
            success_count = (success_count << 8) | b

        pga_map = {0: 1.0, 1: 2.0, 2: 64.0, 3: 128.0}
        rate_map = {0: "10 Hz", 1: "40 Hz", 2: "640 Hz", 3: "1280 Hz"}

        self.current_pga = pga_map.get(pga_code, self.current_pga)
        self.current_sample_rate = rate_map.get(rate_code, self.current_sample_rate)
        self.current_channel_code = channel_code

        channel_label = self.channel_labels.get(channel_code, f"未知({channel_code})")

        # 同步UI
        try:
            self.pga_combo.blockSignals(True)
            self.pga_combo.setCurrentText(str(int(self.current_pga)) if self.current_pga in [1.0, 2.0, 64.0, 128.0] else self.pga_combo.currentText())
            self.pga_combo.blockSignals(False)
        except Exception:
            pass

        try:
            self.sample_rate_combo.blockSignals(True)
            self.sample_rate_combo.setCurrentText(self.current_sample_rate)
            self.sample_rate_combo.blockSignals(False)
        except Exception:
            pass

        try:
            if 0 <= channel_code < self.channel_combo.count():
                self.channel_combo.blockSignals(True)
                self.channel_combo.setCurrentIndex(channel_code)
                self.channel_combo.blockSignals(False)
        except Exception:
            pass

        self.log_message(
            f"📊 Arduino状态: PGA=x{self.current_pga}, 采样率={self.current_sample_rate}, 通道={channel_label}, 成功读取≈{success_count}\n",
            category="status",
        )
    
    def handle_config_ack_frame(self, data):
        """处理配置确认帧"""
        if len(data) < 2:
            return
        
        config_type = data[0]
        value = data[1]
        
        if config_type == 0xA1:  # PGA
            pga_map = {0: 1.0, 1: 2.0, 2: 64.0, 3: 128.0}
            self.current_pga = pga_map.get(value, 128.0)
            self.log_message(f"✅ PGA配置已确认: {self.current_pga}\n", category="status")
            try:
                self.pga_combo.blockSignals(True)
                self.pga_combo.setCurrentText(str(int(self.current_pga)))
                self.pga_combo.blockSignals(False)
            except Exception:
                pass
        elif config_type == 0xA2:  # 采样率
            rate_map = {0: "10 Hz", 1: "40 Hz", 2: "640 Hz", 3: "1280 Hz"}
            self.current_sample_rate = rate_map.get(value, "10 Hz")
            self.log_message(f"✅ 采样率配置已确认: {self.current_sample_rate}\n", category="status")
            try:
                self.sample_rate_combo.blockSignals(True)
                self.sample_rate_combo.setCurrentText(self.current_sample_rate)
                self.sample_rate_combo.blockSignals(False)
            except Exception:
                pass
        elif config_type == 0xA3:  # 通道
            self.current_channel_code = value
            channel_label = self.channel_labels.get(value, f"未知({value})")
            self.log_message(f"✅ 通道配置已确认: {channel_label}\n", category="status")
            try:
                if 0 <= value < self.channel_combo.count():
                    self.channel_combo.blockSignals(True)
                    self.channel_combo.setCurrentIndex(value)
                    self.channel_combo.blockSignals(False)
            except Exception:
                pass
        elif config_type == 0xA4:  # 电源状态
            self.power_down = (value == 1)
            state_text = "已进入Power down" if self.power_down else "已退出Power down"
            self.log_message(f"✅ {state_text}\n", category="status")
            self.statusBar().showMessage(state_text)
        
    def adc_to_voltage(self, adc_value: int) -> float:
        """根据当前PGA和VREF将ADC值转换为电压"""
        try:
            pga = float(self.current_pga)
        except Exception:
            pga = 128.0
        if pga == 0:
            pga = 1.0
        scale = self.vref / (pga * 8388608.0)
        return adc_value * scale

    def adc_to_temperature(self, adc_value: int) -> float:
        """粗略温度换算（与固件默认实现保持一致）"""
        return adc_value * 0.01 - 50.0

    def on_error(self, error_msg):
        """处理错误信息"""
        self.log_message(error_msg + "\n", category="error")
        
    def should_display_line(self, line):
        """判断是否应该显示该行信息"""
        filter_keywords = [
            "CS1237 ADC - Basic Mode",
            "Commands:",
            "Send 's' to stop",
            "=== CS1237 Configuration Mode ===",
            "=== CS1237 配置模式 ===",
            "1. Set PGA Gain",
            "2. Set Sample Rate",
            "3. Back to main menu",
            "请输入选择",
            "Enter your choice",
            "--- PGA Gain Setting ---",
            "--- PGA 增益设置 ---",
            "--- Sample Rate Setting ---",
            "--- 采样率设置 ---",
            "--- 通道设置 ---",
            "PGA = ",
            "Select PGA",
            "Select sample rate",
            "请选择 PGA",
            "请选择采样率",
            "请选择通道",
            "Configuration mode timeout",
            "超时，返回主菜单",
            "Returning to main menu",
            "Invalid choice",
            "PGA set successfully",
            "Sample rate set successfully",
            "Starting continuous reading",
            "Stopping continuous reading",
            "开始连续读取",
            "停止连续读取",
            "Available commands:",
            "可用命令列表",
            "Single read",
            "Continuous read",
            "Configuration mode",
            "Show current configuration",
            "Data not ready",
            "进入 Power down 模式",
            "退出 Power down 模式"
        ]
        
        for keyword in filter_keywords:
            if keyword in line:
                return False
        
        return True
        
    def log_message(self, message, category="general", persist_status=False):
        """在文本区域显示消息，仅保留必要类别，其余转发到状态栏/控制台。"""
        cleaned = message.rstrip()
        category = category or "general"

        show_in_text_area = True
        if getattr(self, 'show_adc_only', False):
            allowed = getattr(self, 'allowed_output_categories', {"adc"})
            show_in_text_area = category in allowed

        if not show_in_text_area:
            print(cleaned)
            try:
                duration = 0 if persist_status else 5000
                self.statusBar().showMessage(cleaned, duration)
            except Exception:
                pass
            return

        try:
            scrollbar = self.text_area.verticalScrollBar()
            try:
                at_bottom = scrollbar.value() >= (scrollbar.maximum() - 20)
            except Exception:
                at_bottom = True

            self.text_area.append(cleaned)

            if at_bottom:
                try:
                    scrollbar.setValue(scrollbar.maximum())
                except Exception:
                    pass
        except Exception:
            try:
                self.text_area.append(cleaned)
            except Exception:
                print(cleaned)
        
    def clear_output(self):
        """清除输出区域和图像数据"""
        self.text_area.clear()
        self.clear_plot()
    
    def show_help_cmd(self):
        """显示Arduino帮助信息"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command('?')
        
    def single_read(self):
        """单次读取数据"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command('R')
        
    def toggle_continuous(self):
        """切换连续读取模式"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        if not self.is_continuous:
            # 🔧 开始新的采集前，只有在数据未保存时才询问是否保存
            if len(self.plot_data_x) > 0 and not self.data_saved:
                self.prompt_save_data()
            
            # 清除图形和时间轴，准备新的采集
            self.clear_plot()
            
            if self.send_command('A'):
                self.is_continuous = True
                self.continuous_btn.setText("停止连续读取")
                # 🔧 开始采样时重置时间起点，让图形从0开始
                self.start_time = time.time()
                self.last_draw_time = 0
                self.data_saved = False  # 标记数据未保存
                self.log_message("✅ 开始连续采样，时间从0开始计时\n", category="status")
        else:
            if self.send_command('s'):
                self.is_continuous = False
                self.continuous_btn.setText("开始连续读取")
                self.log_message("⏸️ 已停止采集，数据保留在图形中\n", category="status")
    
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
                f.write(f"# 输入通道: {self.channel_labels.get(self.current_channel_code, '未知')}\n")
                f.write(f"# 数据点数: {len(self.plot_data_x)}\n")
                f.write("#" + "="*60 + "\n")
                f.write("# 时间(秒)\tADC值\n")
                
                # 写入数据
                for t, v in zip(self.plot_data_x, self.plot_data_y):
                    f.write(f"{t:.3f}\t{v}\n")
            # 标记数据已保存
            self.data_saved = True

            # 同时写入 CSV（与 TXT 同目录、同文件名但扩展名为 .csv）
            try:
                base, _ = os.path.splitext(file_path)
                csv_path = base + '.csv'
                with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                    writer = csv.writer(cf)
                    writer.writerow(['time_s', 'adc'])
                    for t, v in zip(self.plot_data_x, self.plot_data_y):
                        writer.writerow([f"{t:.3f}", v])
                info_msg = f"数据已保存到:\n{file_path}\n{csv_path}"
            except Exception:
                info_msg = f"数据已保存到:\n{file_path}\n(生成 CSV 失败)"

            QMessageBox.information(self, "成功", info_msg)
            self.log_message(f"✅ 数据已导出: {file_path} (同时导出 CSV)", category="result")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存文件失败:\n{str(e)}")

    def on_sequence_finished(self, success, message):
        """命令序列执行完成后的回调"""
        self.log_message(f"SEQUENCER: {message}\n", category="result")
        # 解锁GUI按钮
        self.set_pga_btn.setEnabled(True)
        self.set_rate_btn.setEnabled(True)
        if hasattr(self, 'set_channel_btn'):
            self.set_channel_btn.setEnabled(True)
                
    def set_pga(self):
        """设置PGA增益 (适配Arduino菜单逻辑)"""
        if not self.is_connected or not self.serial_thread:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return

        pga_map = {"1": "0", "2": "1", "64": "2", "128": "3"}
        pga_value = self.pga_combo.currentText()
        
        if pga_value not in pga_map:
            QMessageBox.warning(self, "警告", "请选择有效的PGA值")
            return

        # 锁定按钮，防止重复点击
        self.set_pga_btn.setEnabled(False)
        self.set_rate_btn.setEnabled(False)
        if hasattr(self, 'set_channel_btn'):
            self.set_channel_btn.setEnabled(False)
        if hasattr(self, 'set_channel_btn'):
            self.set_channel_btn.setEnabled(False)

        # 定义与Arduino菜单交互的命令序列
        sequence = [
            ('send', 'C'),
            ('wait_for', ['选择', 'choice', 'Enter your choice']),
            ('send', '1'),
            ('wait_for', ['PGA', 'Gain']),
            ('send', pga_map[pga_value]),
            ('wait_for', ['成功', 'success', 'Success']),
            ('send', '4')
        ]

        # 启动命令序列执行器
        self.sequencer = CommandSequencer(self, sequence)
        self.sequencer.sequence_finished.connect(self.on_sequence_finished)
        self.sequencer.start()

    def set_sample_rate(self):
        """设置采样率 (适配Arduino菜单逻辑)"""
        if not self.is_connected or not self.serial_thread:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return

        rate_map = {"10 Hz": "0", "40 Hz": "1", "640 Hz": "2", "1280 Hz": "3"}
        rate_value = self.sample_rate_combo.currentText()

        if rate_value not in rate_map:
            QMessageBox.warning(self, "警告", "请选择有效的采样率")
            return

        # 锁定按钮
        self.set_pga_btn.setEnabled(False)
        self.set_rate_btn.setEnabled(False)

        # 定义命令序列
        sequence = [
            ('send', 'C'),
            ('wait_for', ['选择', 'choice', 'Enter your choice']),
            ('send', '2'),
            ('wait_for', ['采样率', 'Sample Rate']),
            ('send', rate_map[rate_value]),
            ('wait_for', ['成功', 'success', 'Success']),
            ('send', '4')
        ]
        
        self.sequencer = CommandSequencer(self, sequence)
        self.sequencer.sequence_finished.connect(self.on_sequence_finished)
        self.sequencer.start()

    def set_channel(self):
        """设置输入通道 (适配Arduino菜单逻辑)"""
        if not self.is_connected or not self.serial_thread:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return

        channel_map = {
            "通道A（差分）": "0",
            "保留": "1",
            "温度传感器": "2",
            "内短模式": "3"
        }

        channel_value = self.channel_combo.currentText()
        if channel_value not in channel_map:
            QMessageBox.warning(self, "警告", "请选择有效的通道")
            return

        self.set_pga_btn.setEnabled(False)
        self.set_rate_btn.setEnabled(False)
        self.set_channel_btn.setEnabled(False)

        sequence = [
            ('send', 'C'),
            ('wait_for', ['选择', 'choice', 'Enter your choice']),
            ('send', '3'),
            ('wait_for', ['通道', 'Input Channel']),
            ('send', channel_map[channel_value]),
            ('wait_for', ['成功', 'success', 'Success']),
            ('send', '4')
        ]

        self.sequencer = CommandSequencer(self, sequence)
        self.sequencer.sequence_finished.connect(self.on_sequence_finished)
        self.sequencer.start()
            
    def get_status(self):
        """查询当前配置状态"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command('S')

    def enter_power_down(self):
        """发送进入省电模式命令"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        if self.send_command('D'):
            self.log_message("⚡ 正在请求进入省电模式...\n", category="status")

    def exit_power_down(self):
        """发送退出省电模式命令"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        if self.send_command('U'):
            self.log_message("🔋 正在请求退出省电模式...\n", category="status")
    
    # def is_outlier(self, value):
    #     """
    #     判断数值是否为异常值
    #     使用移动中位数绝对偏差（MAD）方法：基于最近的局部数据窗口判断
    #     这种方法对异常值本身具有鲁棒性，不会被异常值污染
    #     """
    #     if not self.enable_outlier_filter:
    #         return False
        
    #     # 第一层：过滤明显的极端值（硬件错误）
    #     if abs(value) > 8000000:
    #         return True  # 接近24位ADC满量程，可能是硬件错误
        
    #     if len(self.recent_values) < self.min_data_for_filter:
    #         return False  # 数据不足，不进行统计过滤
        
    #     # 使用最近的数据窗口（取最后10-20个点作为局部参考）
    #     window_size = min(20, len(self.recent_values))
    #     local_window = list(self.recent_values)[-window_size:]
        
    #     # 计算中位数（对异常值鲁棒）
    #     sorted_window = sorted(local_window)
    #     n = len(sorted_window)
    #     if n % 2 == 0:
    #         median = (sorted_window[n//2 - 1] + sorted_window[n//2]) / 2.0
    #     else:
    #         median = sorted_window[n//2]
        
    #     # 计算中位数绝对偏差（MAD - Median Absolute Deviation）
    #     absolute_deviations = [abs(x - median) for x in local_window]
    #     sorted_deviations = sorted(absolute_deviations)
    #     if len(sorted_deviations) % 2 == 0:
    #         mad = (sorted_deviations[n//2 - 1] + sorted_deviations[n//2]) / 2.0
    #     else:
    #         mad = sorted_deviations[n//2]
        
    #     # 避免MAD为0的情况（所有数据相同）
    #     if mad < 0.01:
    #         # 使用绝对阈值：偏离中位数超过100认为是异常
    #         deviation = abs(value - median)
    #         return deviation > 100
        
        
    #     #  严格的数量级判断：只有当数值与中位数的绝对值比值相差至少10倍时才判为异常
    #     # 这样可以避免误判正常波动的数据（如 -87840 vs 162761，数量级相近不算异常）
        
    #     abs_value = abs(value)
    #     abs_median = abs(median)
    #     eps = 1e-9  # 避免除零
        
    #     # 计算数量级比值（大值/小值）
    #     if abs_value < eps and abs_median < eps:
    #         # 两者都接近0，不是异常
    #         return False
        
    #     max_val = max(abs_value, abs_median)
    #     min_val = max(min(abs_value, abs_median), eps)
    #     magnitude_ratio = max_val / min_val
        
    # # 调试输出已移除
        
    #     # 只有数量级相差至少10倍才判为异常
    #     if magnitude_ratio < 10.0:
    #         return False
        
    #     # 额外检查：如果中位数非常小（接近0），需要检查绝对偏差
    #     # 例如中位数是1，当前值是100000，这是真正的异常
    #     if abs_median < 100:
    #         # 当中位数很小时，要求绝对差至少达到1000才算异常
    #         absolute_diff = abs(value - median)
    #         return absolute_diff >= 1000
        
    #     # 对于正常范围的中位数，只要数量级相差10倍就算异常
    #     return True


    def is_outlier_in_context(self, value, prev_context, next_ctx_list, local_window=None):
        """基于给定的局部窗口（前/后邻点）判断 value 是否为异常点（单点脉冲）。
        使用 MAD + 数量级判断，返回 True/False。local_window 是一个只包含数值的序列。
        """
        # 改为：只有当该点与前后各4个点的数量级“完全不一样”时才判为异常，
        # 否则不判为异常。
        # local_window 应当为前后邻点的列表（数值部分）。
        # 新逻辑：尝试使用五点法判断（三个连续点的中间点是否为异常）
        # 我们期望传入 prev_context（至少两个之前点）和 next_ctx_list（至少两个之后点），
        # 以及 local_window（前后邻点合并，用于回退计算）。
        if not self.enable_outlier_filter:
            return False, None, None

        # 如果调用方没有提供 prev/next 明确分割，则仍然接受旧的 local_window 用法
        # 但我们的 signature 要求 prev_context, next_ctx_list, local_window;
        # 若传入不全，则回退到保守行为：不判定为异常。
        # 这里确保调用方传入的是 list 类型
        try:
            prev_ctx = prev_context if isinstance(prev_context, list) else []
            next_ctx = next_ctx_list if isinstance(next_ctx_list, list) else []
        except NameError:
            return False, None, None

        eps = 1e-9

        # 五点法要求前后各两个点
        if len(prev_ctx) >= 2 and len(next_ctx) >= 2:
            p1 = prev_ctx[-2]
            p2 = prev_ctx[-1]
            p3 = value
            p4 = next_ctx[0]
            p5 = next_ctx[1]

            a = p2 - p1
            b = p3 - p2
            c_ = p4 - p3
            d = p5 - p4

            def safe_ratio(x, y):
                if abs(y) < eps:
                    return float('inf') if abs(x) >= eps else 1.0
                return abs(x / y)

            ratio_ba = safe_ratio(b, a)
            ratio_cd = safe_ratio(c_, d)

            cond1 = (ratio_ba > 20.0) or (ratio_ba < 1.0 / 20.0)
            cond2 = (ratio_cd > 20.0) or (ratio_cd < 1.0 / 20.0)

            if cond1 and cond2:
                # 将第三个点（p3）视为异常，先用 p1,p2,p4,p5 的均值作为候选
                candidate = (p1 + p2 + p4 + p5) / 4.0
                # 中位数保护：计算邻域中位数，防止极端值传播
                neighbors = [p1, p2, p4, p5]
                sorted_n = sorted(neighbors)
                median_neighbors = float(sorted_n[len(sorted_n) // 2])
                # 如果 candidate 与中位数差距过大，则以中位数为最终替换值
                try:
                    if abs(candidate - median_neighbors) > max(3.0 * abs(median_neighbors), 1000):
                        replacement = int(round(median_neighbors))
                    else:
                        replacement = int(round(candidate))
                except Exception:
                    replacement = int(round(candidate))
                return True, replacement, None

            # 额外放宽规则：当 b/a 极端（cond1 为真）但 c/d 未必极端时，
            # 仍可能是单点孤立脉冲——尤其是当 p3 的绝对值远大于邻域中位数时。
            # 为避免漏判像 -8348502 这样的极端值，加入基于绝对阈值与中位数比值的检测。
            try:
                neighbors = [p1, p2, p4, p5]
                sorted_n = sorted(neighbors)
                median_neighbors = float(sorted_n[len(sorted_n) // 2])
            except Exception:
                median_neighbors = 0.0

            try:
                abs_p3 = abs(p3)
            except Exception:
                abs_p3 = 0.0

            # 阈值：绝对值阈 = 1e6；或相对于邻域中位数的比值阈 = 10
            try:
                ratio_to_median = abs_p3 / (abs(median_neighbors) if abs(median_neighbors) > eps else eps)
            except Exception:
                ratio_to_median = float('inf') if abs_p3 > 0 else 0.0

            if cond1 and (abs_p3 > 1_000_000 or ratio_to_median > 10.0):
                candidate = (p1 + p2 + p4 + p5) / 4.0
                try:
                    if abs(candidate - median_neighbors) > max(3.0 * abs(median_neighbors), 1000):
                        replacement = int(round(median_neighbors))
                    else:
                        replacement = int(round(candidate))
                except Exception:
                    replacement = int(round(candidate))
                return True, replacement, None

            # 新增：若 c 和 d 的比值极端，则判定 p3 和 p4 都为异常（邻近双点）
            # 原先使用 p2->p5 插值替换可能会被极端后点放大导致不合理的大值，
            # 这里改为使用邻点的中位数作为更稳健的替换值（p1,p2,p4,p5 的中位数）。
            if (ratio_cd > 20.0) or (ratio_cd < 1.0 / 20.0):
                # 邻近双点异常：为保持原曲线趋势，不直接将两点替换为相同值。
                # 使用 p2->p5 的线性插值来生成两个不同且平滑的替换值。
                try:
                    slope = (p5 - p2) / 3.0
                    repl_p3 = p2 + slope     # 一步之差
                    repl_p4 = p2 + 2 * slope # 两步之差
                except Exception:
                    # 回退到稳健的中位数方案
                    neighbors = [p1, p2, p4, p5]
                    sorted_n = sorted(neighbors)
                    median_val = float(sorted_n[len(sorted_n) // 2])
                    repl_p3 = median_val
                    repl_p4 = median_val

                # 对两个替换值做中位数限幅保护，避免异常放大；同时尽量保留两点差异以保持趋势
                neighbors = [p1, p2, p4, p5]
                sorted_n = sorted(neighbors)
                median_neighbors = float(sorted_n[len(sorted_n) // 2])

                # 允许的最大偏差：取中位数的10%或1000，取较大者，防止过度偏移
                try:
                    max_dev = max(int(abs(median_neighbors) * 0.1), 1000)
                except Exception:
                    max_dev = 1000

                def clamp_within(val, med, dev):
                    low = med - dev
                    high = med + dev
                    try:
                        v = float(val)
                        if v < low:
                            return int(round(low))
                        if v > high:
                            return int(round(high))
                        return int(round(v))
                    except Exception:
                        return int(round(med))

                c3 = clamp_within(repl_p3, median_neighbors, max_dev)
                c4 = clamp_within(repl_p4, median_neighbors, max_dev)

                # 若两者在限幅后仍相同，则制造一个最小差异以保留曲线抖动/趋势
                if c3 == c4:
                    # 根据插值方向决定偏移符号
                    # 使用较小的偏移，至少为1，且不超过 max_dev
                    jitter = max(1, min(int(max_dev * 0.01), max_dev))
                    # 应用偏移，确保不会越过合理边界
                    c3 = max(int(round(median_neighbors - jitter)), -8388608)
                    c4 = min(int(round(median_neighbors + jitter)), 8388607)

                return True, c3, c4

        # 回退到原有更严格的数量级比较（针对前后共8点）
        if not local_window or len(local_window) < 8:
            return False, None, None

        abs_val = abs(value)

        all_diff = True
        for n in local_window:
            abs_n = abs(n)
            if abs_val < eps and abs_n < eps:
                all_diff = False
                break

            big = max(abs_val, abs_n)
            small = max(min(abs_val, abs_n), eps)
            ratio = big / small
            if ratio < 5.0:
                all_diff = False
                break

        return (all_diff, None, None) if all_diff else (False, None, None)

    def _is_spike_between(self, prev_v, curr_v, next_v):
        """
        使用前三点（prev, curr, next）判断 curr 是否为单点突变（脉冲/尖峰）。
        原理：若 curr 与前后两点的线性插值中值偏差远大于前后两点之间的正常差异，则判为异常。
        返回 (is_outlier, replacement_value)
        """
        # 严格按“单点脉冲”定义判断：
        # - 中点相对于相邻两点要至少大一个数量级（>=10x），
        # - 两个邻点彼此接近（说明中点是孤立突变，而不是邻点本身也在变化），
        # - 中点的高值不应被后点保持（如果 next 也很大，则视为持续增高，不判为单点脉冲）。
        try:
            interp = (prev_v + next_v) / 2.0
        except Exception:
            return False, curr_v

        # 绝对值尺度
        abs_prev = abs(prev_v)
        abs_curr = abs(curr_v)
        abs_next = abs(next_v)

        # 基本保护，避免除零
        eps = 1e-9

        # 邻点最大值，用来判断中点是否显著更大
        max_neighbor = max(abs_prev, abs_next, eps)

        # 邻点彼此接近：要求 max/min <= 2（可调），若邻点之一为0则允许小偏差
        min_neighbor = min(abs_prev if abs_prev > eps else max_neighbor,
                           abs_next if abs_next > eps else max_neighbor)
        neighbors_ratio = max_neighbor / (min_neighbor + eps)

        # 中点要比邻点大多少才算“数量级更大”（要求 >= 10）
        magnitude_ratio = abs_curr / max_neighbor

        # 如果后点也接近中点（说明不是孤立），则不算单点脉冲
        next_vs_curr = abs_next / (abs_curr + eps)

        # 判断条件：邻点彼此接近 && 中点相比邻点至少 10x && 后点远小于中点
        if neighbors_ratio <= 2.0 and magnitude_ratio >= 10.0 and next_vs_curr < 0.5:
            # 使用前后线性插值作为替换值（更合理），并返回 True
            return True, interp

        # 其他情况不认为是单点脉冲
        return False, curr_v

    def _flush_processing_buffer(self, force=False):
        """
        将 processing_buffer 中的点按顺序处理并移动到绘图数据中。
        如果 buffer 长度为3，则判断并可能替换中间点后将中间点写入绘图数据。
        如果 force=True，则会把剩余的 1-2 个点也按原样输出（用于重置/退出时刷新残留数据）。
        """
        # 只在有足够点或被强制刷新时写入，使用锁保护以避免竞态
        with self.buffer_lock:
            while True:
                if len(self.processing_buffer) >= 3:
                    try:
                        (t0, v0) = self.processing_buffer[0]
                        (t1, v1) = self.processing_buffer[1]
                        (t2, v2) = self.processing_buffer[2]
                    except IndexError:
                        break

                    # 基于邻点判断中间点是否为脉冲/异常
                    is_spike, replacement = self._is_spike_between(v0, v1, v2)
                    if is_spike and self.enable_outlier_filter:
                        self.outlier_count += 1
                        # 异常值已静默替换，不显示日志

                        v_emit = int(replacement)
                    else:
                        v_emit = v1

                    # 写入最近值与绘图数据（使用处理后的中间值）
                    self.recent_values.append(v_emit)
                    self.plot_data_x.append(t1)
                    self.plot_data_y.append(v_emit)

                    # 弹出左侧一个元素（deque 固定长度）
                    try:
                        self.processing_buffer.popleft()
                    except IndexError:
                        # 已被其他逻辑改变，安全退出
                        break
                    # 处理后，循环继续，直到 buffer 长度 < 3
                    continue
                else:
                    # len < 3
                    if force and len(self.processing_buffer) > 0:
                        # 按顺序把剩余点写入，避免丢数据
                        while True:
                            try:
                                t, v = self.processing_buffer.popleft()
                            except IndexError:
                                break
                            self.recent_values.append(v)
                            self.plot_data_x.append(t)
                            self.plot_data_y.append(v)
                    break
        
    def extract_and_plot_adc(self, line):
        """从串口数据中提取ADC值并更新图形"""
        # 匹配 RAW ADC: 后面的数值（支持带符号或不带符号）
        match = re.search(r'RAW ADC:\s*(-?\d+)', line)
        if match:
            try:
                raw = int(match.group(1))
                
                # 如果 Arduino 已经输出负数（带'-'），raw 会是负值
                if raw < 0:
                    signed = raw
                else:
                    # 将 24-bit 原始无符号值转换为有符号值（two's complement）
                    if raw & 0x800000:
                        signed = raw - 0x1000000
                    else:
                        signed = raw

                # 异常值检测与替换：不使用 is_outlier() 的第一层统计检测，
                # 改为基于前后文（is_outlier_in_context）判断以减少误判。
                current_time = time.time() - self.start_time
                final_value = signed  # 默认使用原始值
                
                # 第一层统计检测已禁用，直接使用原始解析值进入缓冲区

                # 计算电压
                # (已移除电压计算，pga 不再需要)

                # 将点放入新的 buffered_points 缓冲区，等待足够的前/后点用于判定
                try:
                    with self.buffer_lock:
                        self.buffered_points.append((current_time, final_value))
                        self.total_received += 1
                except Exception:
                    try:
                        self.buffered_points.append((current_time, final_value))
                        self.total_received += 1
                    except Exception:
                        pass

                # 初始阶段：若总接收数少于 min_points_before_plot，则仅收集不进行任何处理和绘图
                if self.total_received < getattr(self, 'min_points_before_plot', 50):
                    # 不触发绘图，等待更多点
                    return

                # 当缓冲区中至少有 lookahead 个后续点时，可以对左侧最早的点进行基于前后文的判定
                try:
                    # 反复处理直到缓冲区长度不足以提供后向上下文
                    while len(self.buffered_points) > self.lookahead:
                        # 取候选点（左侧第一个）但不立即弹出
                        t_candidate, v_candidate = self.buffered_points[0]

                        # 准备前/后各4个点作为局部上下文（优先使用前4点与后4点）
                        prev_needed = 4
                        next_needed = 4
                        # 尝试从已写出的绘图数据中获取前向上下文；若不足则回退到 recent_values
                        prev_context = []
                        if prev_needed > 0:
                            if len(self.plot_data_y) >= prev_needed:
                                prev_context = list(self.plot_data_y)[-prev_needed:]
                            else:
                                if len(self.recent_values) >= prev_needed:
                                    prev_context = list(self.recent_values)[-prev_needed:]
                                else:
                                    prev_context = list(self.recent_values)

                        # 准备后向上下文：从 buffered_points 中取若干点（不含候选点）
                        next_ctx_list = [v for (_, v) in list(self.buffered_points)[1:1 + next_needed]]

                        # 合成局部窗口（前4 + 后4）
                        local_window = list(prev_context) + list(next_ctx_list)

                        # 如果候选值非常大，记录简短的上下文以便调试
                        try:
                            if abs(v_candidate) > 1000000:
                                self.log_message(
                                    f"DEBUG_CONTEXT: candidate={v_candidate}, prev={prev_context}, next={next_ctx_list}, local_len={len(local_window)}\n",
                                    category="debug",
                                )
                        except Exception:
                            pass

                        # 使用局部窗口判断是否为异常（只有当与前后4点数量级完全不一样才判为异常）
                        is_outlier_ctx = False
                        replacement_value = None
                        replacement_next = None
                        try:
                            is_outlier_ctx, replacement_value, replacement_next = self.is_outlier_in_context(
                                v_candidate, prev_context, next_ctx_list, local_window
                            )
                        except Exception:
                            is_outlier_ctx = False
                            replacement_value = None
                            replacement_next = None

                        # 如果要替换 buffer 中的下一个点，则直接写入
                        try:
                            if replacement_next is not None and len(self.buffered_points) > 1:
                                t_next = self.buffered_points[1][0]
                                self.buffered_points[1] = (t_next, replacement_next)
                        except Exception:
                            pass

                        if is_outlier_ctx:
                            # 统计替换计数
                            self.outlier_count += 1
                            try:
                                # 优先使用 is_outlier_in_context 给出的替换值
                                if replacement_value is not None:
                                    v_emit = int(replacement_value)
                                elif len(local_window) >= 8:
                                    mean_val = sum(local_window) / len(local_window)
                                    v_emit = int(round(mean_val))
                                else:
                                    # 回退到使用局部窗口的中位数（兼容早期数据不足情形）
                                    if len(local_window) > 0:
                                        sorted_win = sorted(local_window)
                                        median = sorted_win[len(sorted_win) // 2]
                                        v_emit = int(median)
                                    else:
                                        v_emit = int(v_candidate)
                                # 异常值已静默替换，不显示日志
                            except Exception:
                                v_emit = int(v_candidate)
                        else:
                            v_emit = int(v_candidate)

                        # 将处理后的候选点写入历史与绘图数据
                        self.recent_values.append(v_emit)
                        self.plot_data_x.append(t_candidate)
                        self.plot_data_y.append(v_emit)
                        
                        # 标记数据未保存（有新数据添加）
                        self.data_saved = False

                        # 弹出已处理的候选点
                        try:
                            self.buffered_points.popleft()
                        except Exception:
                            break
                except Exception:
                    # 在任何处理异常时跳出，等待后续数据
                    pass

                # 限制绘图频率，避免过度更新
                now = time.time()
                if now - self.last_draw_time >= self.draw_interval:
                    self.update_plot()
                    self.last_draw_time = now
            except ValueError:
                # 无效的数值格式时忽略并继续
                pass
                
    def update_plot(self):
        """更新图形显示（600秒滚动窗口 = 10分钟）"""
        try:
            # 延迟绘图：在收集到足够多的数据点前不进行绘图，确保异常值已有机会被处理
            if len(self.plot_data_x) < getattr(self, 'min_points_before_plot', 1):
                return
            if len(self.plot_data_x) == 0 or len(self.plot_data_y) == 0:
                return
                
            x_data = list(self.plot_data_x)
            y_data = list(self.plot_data_y)
            
            # 根据显示模式决定展示的数据范围：
            # - 累计模式（self.cumulative_mode=True）：显示从0到当前时间的所有点
            # - 滑动窗口模式：只显示最近 self.time_window 秒的数据
            current_time = x_data[-1] if x_data else 0

            if getattr(self, 'cumulative_mode', False):
                display_x = x_data
                display_y = y_data
            else:
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
            # X轴范围设置 - 智能调整（仅在上一次自动设置未被用户改动时才覆盖视图）
            x_min, x_max = min(display_x), max(display_x)
            x_range = x_max - x_min

            if x_range > 0:
                x_margin = max(0.5, x_range * 0.02)
                desired_xlim = (x_min - x_margin, x_max + x_margin)
            else:
                desired_xlim = (max(0, x_min - 1), x_min + self.time_window)

            try:
                current_xlim = tuple(float(v) for v in self.ax.get_xlim())
            except Exception:
                current_xlim = None

            # 只有当当前轴范围等于上一次自动设置的范围时（也即没有用户交互）才覆盖
            if self._last_auto_xlim is None or current_xlim == tuple(float(v) for v in self._last_auto_xlim):
                try:
                    self.ax.set_xlim(desired_xlim)
                except Exception:
                    self.ax.set_xlim(desired_xlim[0], desired_xlim[1])
                self._last_auto_xlim = desired_xlim

            # Y轴范围设置 - 平滑智能缩放（确保曲线连续性）
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

            # 🔧 平滑过渡：确保Y轴不会剧烈跳动
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

            # 只有在用户未手动调整 Y 视图时才覆盖 Y 轴范围
            try:
                current_ylim = tuple(float(v) for v in self.ax.get_ylim())
            except Exception:
                current_ylim = None

            desired_ylim = (self.current_y_min, self.current_y_max)
            if self._last_auto_ylim is None or current_ylim == tuple(float(v) for v in self._last_auto_ylim):
                try:
                    self.ax.set_ylim(desired_ylim)
                except Exception:
                    self.ax.set_ylim(desired_ylim[0], desired_ylim[1])
                self._last_auto_ylim = desired_ylim

            # 自动调整刻度（无论 y_range 是否为0 都要执行）
            self.auto_adjust_ticks(x_range, y_range, len(display_x))

            # 小图：显示最近20秒的数据（右侧小图）
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

                # 重新绘制主/小画布
                self.small_canvas.draw_idle()
            except Exception:
                pass

            # 重新绘制主画布
            self.canvas.draw_idle()
        except Exception as e:
            # 捕获绘图时的异常，记录到输出区域，避免程序崩溃
            try:
                self.log_message(f"绘图错误: {str(e)}\n", category="error")
            except Exception:
                print(f"绘图错误: {e}")
            
    def auto_adjust_ticks(self, x_range, y_range, data_count):
        """根据数据范围和密度智能调整刻度间距"""
        
        # ========== X轴刻度（时间轴）优化 ==========
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
        
        # ========== Y轴刻度（ADC值）优化 ==========
        # 根据数值范围智能选择刻度数量
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
        
        # 使用智能定位器，自动选择合适的刻度值
        # 允许浮点主刻度，避免整数强制导致范围/负值问题
        self.ax.yaxis.set_major_locator(MaxNLocator(nbins=y_ticks, integer=False, prune='both'))
        self.ax.yaxis.set_minor_locator(AutoMinorLocator(y_minor_divs))
        
        # ========== 网格线优化 ==========
        # 根据数据密度调整网格透明度
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
        
        # ========== 刻度标签格式化 ==========
        # 根据数值大小智能选择显示格式
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
        self.plot_data_x.clear()
        self.plot_data_y.clear()
        self.line.set_data([], [])
        
        # 清除小图数据
        self.small_line.set_data([], [])
        
        # 重置Y轴平滑控制
        self.current_y_min = None
        self.current_y_max = None
        
        # 重置视图跟踪变量，确保下次采集能从0开始
        self._last_auto_xlim = None
        self._last_auto_ylim = None
        
        # 重置为默认视图
        self.ax.set_xlim(0, self.time_window)
        self.ax.set_ylim(-100, 100)
        
        # 重置网格
        self.ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.8)
        self.ax.grid(True, which='minor', alpha=0.1, linestyle=':', linewidth=0.5)
        
        # 重置刻度格式
        self.ax.yaxis.set_major_formatter(ScalarFormatter())
        
        # 清除处理缓冲区，避免遗留未处理点
        try:
            self.processing_buffer.clear()
            self.buffered_points.clear()
        except Exception:
            pass
        
        # 清除异常值统计数据
        self.recent_values.clear()
        self.outlier_count = 0
        
        # 重置总接收计数
        self.total_received = 0
        
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
