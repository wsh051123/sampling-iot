import sys
import time
import re
from collections import deque
from datetime import datetime
import threading

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QPushButton, 
                             QTextEdit, QGroupBox, QGridLayout, QMessageBox,
                             QCheckBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, AutoMinorLocator, FuncFormatter, ScalarFormatter

# 设置matplotlib中文字体
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class SerialThread(QThread):
    """串口读取线程"""
    data_received = pyqtSignal(str)  # 接收到数据的信号
    error_occurred = pyqtSignal(str)  # 错误信号
    
    def __init__(self, serial_port):
        super().__init__()
        self.serial_port = serial_port
        self.running = True
        
    def run(self):
        """线程运行函数"""
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting > 0:
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        self.data_received.emit(line)
            except Exception as e:
                if self.running:
                    self.error_occurred.emit(f"读取错误: {str(e)}")
                break
            time.sleep(0.01)
    
    def stop(self):
        """停止线程"""
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
        
        # 当前配置状态
        self.current_pga = 128.0
        self.current_sample_rate = "10 Hz"
        
        # 绘图数据
        self.plot_data_x = deque(maxlen=1000)  # 增加缓冲区以支持更多数据点
        self.plot_data_y = deque(maxlen=1000)
        self.start_time = time.time()
        
        # 显示模式（固定为600秒滚动窗口 = 10分钟）
        self.time_window = 600.0
        
        # 绘图优化参数
        self.last_draw_time = 0
        self.draw_interval = 0.05  # 最小绘图间隔（秒），避免过于频繁更新
        
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
        self.baud_combo.setCurrentText("9600")
        self.baud_combo.setMinimumHeight(25)
        port_layout.addWidget(self.baud_combo, 1, 1, 1, 2)
        
        self.connect_btn = QPushButton("打开串口")
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
        
        config_group.setLayout(config_layout)
        left_layout.addWidget(config_group)
        
        # 4. 图形控制组
        plot_control_group = QGroupBox("图形控制")
        plot_control_layout = QVBoxLayout()
        plot_control_layout.setSpacing(8)
        
        clear_plot_btn = QPushButton("清除图形")
        clear_plot_btn.setMinimumHeight(32)
        clear_plot_btn.clicked.connect(self.clear_plot)
        plot_control_layout.addWidget(clear_plot_btn)
        
        reset_time_btn = QPushButton("重置时间")
        reset_time_btn.setMinimumHeight(32)
        reset_time_btn.clicked.connect(self.reset_time)
        plot_control_layout.addWidget(reset_time_btn)
        
        plot_control_group.setLayout(plot_control_layout)
        left_layout.addWidget(plot_control_group)
        
        # 5. 数据过滤组
        filter_group = QGroupBox("数据过滤")
        filter_layout = QVBoxLayout()
        filter_layout.setSpacing(8)
        
        self.filter_checkbox = QCheckBox("启用异常值过滤")
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.stateChanged.connect(self.toggle_filter)
        filter_layout.addWidget(self.filter_checkbox)
        
        # 显示过滤统计
        self.filter_stats_label = QLabel("已过滤: 0 个异常值")
        self.filter_stats_label.setStyleSheet("color: #666; font-size: 10px;")
        filter_layout.addWidget(self.filter_stats_label)
        
        filter_group.setLayout(filter_layout)
        left_layout.addWidget(filter_group)
        
        # 添加弹簧，将控件推到顶部
        left_layout.addStretch()
        
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
        output_layout.setContentsMargins(5, 10, 5, 5)
        
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setMinimumHeight(150)
        self.text_area.setMaximumHeight(200)
        font = QFont("Consolas", 9)
        self.text_area.setFont(font)
        output_layout.addWidget(self.text_area)
        
        output_group.setLayout(output_layout)

        # --- 右上区域：数据输出（左） + 最近20s小图（右） ---
        # 创建右上横向布局，将数据输出与小图并列
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.setContentsMargins(5, 5, 5, 5)

        # 调整数据输出区高度，使其更小（用户要求）
        self.text_area.setMinimumHeight(40)
        self.text_area.setMaximumHeight(100)

        # 将 output_group 放入左侧（占比较小，以便给小图更多空间）
        top_row.addWidget(output_group, stretch=1)

        # 右侧小图（创建并在右上显示，增大尺寸以便更清晰）
        self.small_fig = Figure(figsize=(5, 3), dpi=100)
        self.small_ax = self.small_fig.add_subplot(111)
        self.small_ax.set_title('最近 20s', fontsize=10)
        self.small_ax.set_xlabel('秒', fontsize=9)
        self.small_ax.set_ylabel('ADC', fontsize=9)
        self.small_ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.6)
        self.small_ax.set_facecolor('#ffffff')
        self.small_line, = self.small_ax.plot([], [], 'r-', linewidth=1.2, antialiased=True)
        self.small_canvas = FigureCanvas(self.small_fig)
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
        self.ax.set_title('CS1237 ADC 实时数据', fontsize=12, fontweight='bold')
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
            self.connect_btn.setText("关闭串口")
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
        self.connect_btn.setText("打开串口")
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
                self.log_message(f"发送命令错误: {str(e)}\n")
                return False
        else:
            QMessageBox.warning(self, "警告", "串口未连接")
            return False
            
    def on_data_received(self, line):
        """处理接收到的串口数据"""
        # 调试：显示所有接收到的原始数据
        print(f"DEBUG - 接收到原始数据: {repr(line)}")
        
        # 过滤不需要显示的信息
        if self.should_display_line(line):
            self.log_message(line + "\n")
        
        # 提取ADC数据并更新图形
        self.extract_and_plot_adc(line)
        
    def on_error(self, error_msg):
        """处理错误信息"""
        self.log_message(error_msg + "\n")
        
    def should_display_line(self, line):
        """判断是否应该显示该行信息"""
        filter_keywords = [
            "CS1237 ADC - Basic Mode",
            "Commands:",
            "Send 's' to stop",
            "=== CS1237 Configuration Mode ===",
            "1. Set PGA Gain",
            "2. Set Sample Rate",
            "3. Back to main menu",
            "Enter your choice",
            "--- PGA Gain Setting ---",
            "--- Sample Rate Setting ---",
            "PGA = ",
            "Select PGA",
            "Select sample rate",
            "Configuration mode timeout",
            "Returning to main menu",
            "Invalid choice",
            "PGA set successfully",
            "Sample rate set successfully",
            "Starting continuous reading",
            "Stopping continuous reading",
            "Available commands:",
            "Single read",
            "Continuous read",
            "Configuration mode",
            "Show current configuration",
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
            if self.send_command('A'):
                self.is_continuous = True
                self.continuous_btn.setText("停止连续读取")
        else:
            if self.send_command('s'):
                self.is_continuous = False
                self.continuous_btn.setText("开始连续读取")
                
    def set_pga(self):
        """设置PGA增益"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        pga_map = {"1": "0", "2": "1", "64": "2", "128": "3"}
        pga_value = self.pga_combo.currentText()
        
        if pga_value in pga_map:
            if self.send_command('C', delay=0.2):
                if self.send_command('1', delay=0.2):
                    if self.send_command(pga_map[pga_value], delay=0.2):
                        self.current_pga = float(pga_value)
        else:
            QMessageBox.warning(self, "警告", "请选择有效的PGA值")
            
    def set_sample_rate(self):
        """设置采样率"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
            
        rate_map = {"10 Hz": "0", "40 Hz": "1", "640 Hz": "2", "1280 Hz": "3"}
        rate_value = self.sample_rate_combo.currentText()
        
        if rate_value in rate_map:
            if self.send_command('C', delay=0.2):
                if self.send_command('2', delay=0.2):
                    if self.send_command(rate_map[rate_value], delay=0.2):
                        self.current_sample_rate = rate_value
        else:
            QMessageBox.warning(self, "警告", "请选择有效的采样率")
            
    def get_status(self):
        """查询当前配置状态"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口")
            return
        self.send_command('S')
    
    def is_outlier(self, value):
        """
        判断数值是否为异常值
        使用移动中位数绝对偏差（MAD）方法：基于最近的局部数据窗口判断
        这种方法对异常值本身具有鲁棒性，不会被异常值污染
        """
        if not self.enable_outlier_filter:
            return False
        
        # 第一层：过滤明显的极端值（硬件错误）
        if abs(value) > 8000000:
            return True  # 接近24位ADC满量程，可能是硬件错误
        
        if len(self.recent_values) < self.min_data_for_filter:
            return False  # 数据不足，不进行统计过滤
        
        # 使用最近的数据窗口（取最后10-20个点作为局部参考）
        window_size = min(20, len(self.recent_values))
        local_window = list(self.recent_values)[-window_size:]
        
        # 计算中位数（对异常值鲁棒）
        sorted_window = sorted(local_window)
        n = len(sorted_window)
        if n % 2 == 0:
            median = (sorted_window[n//2 - 1] + sorted_window[n//2]) / 2.0
        else:
            median = sorted_window[n//2]
        
        # 计算中位数绝对偏差（MAD - Median Absolute Deviation）
        absolute_deviations = [abs(x - median) for x in local_window]
        sorted_deviations = sorted(absolute_deviations)
        if len(sorted_deviations) % 2 == 0:
            mad = (sorted_deviations[n//2 - 1] + sorted_deviations[n//2]) / 2.0
        else:
            mad = sorted_deviations[n//2]
        
        # 避免MAD为0的情况（所有数据相同）
        if mad < 0.01:
            # 使用绝对阈值：偏离中位数超过100认为是异常
            deviation = abs(value - median)
            return deviation > 100
        
        # 使用修正的MAD作为尺度估计
        # 标准正态分布下，MAD * 1.4826 ≈ 标准差
        scale = mad * 1.4826

        # 计算修正Z分数（Modified Z-score）
        modified_z_score = abs(value - median) / scale

        # 阈值：通常使用3.5作为异常值阈值（保留，可调整）
        threshold = 3.5

        # 额外增强：只有当偏离达到“一个数量级”（相对于局部水平至少 10 倍）时才判为异常
        # 计算相对偏差（相对于局部中位数）
        base = max(abs(median), 1e-6)
        relative_ratio = abs(value - median) / base

        # 当局部中位数非常小（接近 0）时，仍需使用绝对阈值作为回退判断
        if abs(median) < 1.0:
            # 使用绝对差 > 1000 作为更严格的回退阈值（可以调整）
            absolute_magnitude_ok = abs(value - median) >= 1000
        else:
            absolute_magnitude_ok = relative_ratio >= 10.0

        # 只有同时满足统计异常（modified z）和数量级差异（absolute_magnitude_ok）才判为异常
        return (modified_z_score > threshold) and absolute_magnitude_ok

    def is_outlier_in_context(self, value, local_window):
        """基于给定的局部窗口（前/后邻点）判断 value 是否为异常点（单点脉冲）。
        使用 MAD + 数量级判断，返回 True/False。local_window 是一个只包含数值的序列。
        """
        if not self.enable_outlier_filter:
            return False

        if not local_window or len(local_window) < 1:
            return False

        # 计算中位数
        sorted_window = sorted(local_window)
        n = len(sorted_window)
        if n % 2 == 0:
            median = (sorted_window[n//2 - 1] + sorted_window[n//2]) / 2.0
        else:
            median = sorted_window[n//2]

        # 计算MAD
        absolute_deviations = [abs(x - median) for x in local_window]
        sorted_deviations = sorted(absolute_deviations)
        if len(sorted_deviations) % 2 == 0:
            mad = (sorted_deviations[n//2 - 1] + sorted_deviations[n//2]) / 2.0
        else:
            mad = sorted_deviations[n//2]

        # 退化情况
        if mad < 0.01:
            return abs(value - median) > 100

        scale = mad * 1.4826
        modified_z_score = abs(value - median) / scale
        threshold = 3.5

        base = max(abs(median), 1e-6)
        relative_ratio = abs(value - median) / base

        if abs(median) < 1.0:
            absolute_magnitude_ok = abs(value - median) >= 1000
        else:
            absolute_magnitude_ok = relative_ratio >= 10.0

        return (modified_z_score > threshold) and absolute_magnitude_ok

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
                        # note: update_filter_stats will call clear_plot which may acquire GUI resources; keep minimal here
                        try:
                            self.update_filter_stats()
                        except Exception:
                            pass
                        try:
                            self.log_message(f"⚠️ 单点脉冲已平滑替换: {v1} -> {int(replacement)} (共过滤 {self.outlier_count} 个)")
                        except Exception:
                            pass

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

                # 🔧 异常值检测与替换（不跳过，用合理值替换）
                current_time = time.time() - self.start_time
                final_value = signed  # 默认使用原始值
                
                if self.is_outlier(signed):
                    # 检测到异常值
                    self.outlier_count += 1
                    self.update_filter_stats()
                    self.log_message(f"⚠️ 异常值已过滤并替换: {signed} (共过滤 {self.outlier_count} 个)")
                    
                    # 🔧 用合理的值替换异常值
                    if len(self.recent_values) >= 1:
                        # 使用最近数据的中位数作为替换值
                        window_size = min(10, len(self.recent_values))
                        recent_window = list(self.recent_values)[-window_size:]
                        sorted_window = sorted(recent_window)
                        median_idx = len(sorted_window) // 2
                        final_value = sorted_window[median_idx]
                    elif len(self.plot_data_y) >= 1:
                        # 如果recent_values为空，使用最后一个绘图值
                        final_value = self.plot_data_y[-1]
                    else:
                        # 完全没有历史数据，使用0
                        final_value = 0

                # 计算电压
                try:
                    pga = float(self.current_pga) if hasattr(self, 'current_pga') else 128.0
                except Exception:
                    pga = 128.0
                voltage = (final_value / 8388607.0) * (2.5 / pga)

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

                        # 准备前向上下文：取已处理的最近若干点
                        prev_needed = max(0, self.context_window // 2)
                        prev_context = []
                        if len(self.plot_data_y) > 0 and prev_needed > 0:
                            prev_context = list(self.plot_data_y)[-prev_needed:]

                        # 准备后向上下文：从 buffered_points 中取若干点（不含候选点）
                        next_ctx_list = [v for (_, v) in list(self.buffered_points)[1:1 + self.lookahead]]

                        # 合成局部窗口（仅数值部分）
                        local_window = prev_context + next_ctx_list

                        # 使用局部窗口判断是否为异常（孤立的数量级突变）
                        is_outlier_ctx = False
                        try:
                            is_outlier_ctx = self.is_outlier_in_context(v_candidate, local_window)
                        except Exception:
                            is_outlier_ctx = False

                        if is_outlier_ctx:
                            # 统计替换计数与日志
                            self.outlier_count += 1
                            try:
                                self.update_filter_stats()
                            except Exception:
                                pass
                            try:
                                # 使用局部窗口的中位数作为替代值（更鲁棒）
                                if len(local_window) > 0:
                                    sorted_win = sorted(local_window)
                                    median = sorted_win[len(sorted_win) // 2]
                                    v_emit = int(median)
                                else:
                                    v_emit = int(v_candidate)
                                self.log_message(f"⚠️ 单点脉冲（基于前后文）已替换: {v_candidate} -> {v_emit} (共过滤 {self.outlier_count} 个)")
                            except Exception:
                                v_emit = int(v_candidate)
                        else:
                            v_emit = int(v_candidate)

                        # 将处理后的候选点写入历史与绘图数据
                        self.recent_values.append(v_emit)
                        self.plot_data_x.append(t_candidate)
                        self.plot_data_y.append(v_emit)

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
            except ValueError as e:
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
            
            # 设置平滑后的Y轴范围
            self.ax.set_ylim(self.current_y_min, self.current_y_max)

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
                self.log_message(f"绘图错误: {str(e)}\n")
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
        # 清除处理缓冲区，避免遗留未处理点
        try:
            self.processing_buffer.clear()
        except Exception:
            pass

        self.canvas.draw()
        
    def reset_time(self):
        """重置时间起点"""
        self.start_time = time.time()
        self.last_draw_time = 0
        self.clear_plot()
    
    def toggle_filter(self, state):
        """切换异常值过滤功能"""
        self.enable_outlier_filter = (state == 2)  # Qt.CheckState.Checked = 2
        status = "已启用" if self.enable_outlier_filter else "已禁用"
        self.log_message(f"异常值过滤功能 {status}\n")
        
    def update_filter_stats(self):
        """更新过滤统计信息"""
        if hasattr(self, 'filter_stats_label'):
            self.filter_stats_label.setText(f"已过滤: {self.outlier_count} 个异常值")
        # 仅重置绘图时间戳以便尽快刷新显示，但不要清空历史数据（避免在检测到异常时丢失前面的曲线）
        self.last_draw_time = 0
        try:
            # 请求一次重绘以更新 UI（不清除数据）
            self.update_plot()
        except Exception:
            pass
            
    def closeEvent(self, event):
        """窗口关闭事件"""
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
