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
        self.setWindowTitle("CS1237 ADC 控制器 - 优化版")
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
        self.plot_data_x = deque(maxlen=1000)
        self.plot_data_y = deque(maxlen=1000)
        self.start_time = time.time()
        
        # 显示模式（固定为600秒滚动窗口 = 10分钟）
        self.time_window = 600.0
        
        # 绘图优化参数
        self.last_draw_time = 0
        self.draw_interval = 0.05  # 最小绘图间隔（秒）
        
        # Y轴范围平滑控制
        self.current_y_min = None
        self.current_y_max = None
        self.y_range_smooth_factor = 0.3
        
        # 🔧 优化后的异常值过滤参数（仅过滤数量级差异极大的异常值）
        self.enable_outlier_filter = True  # 是否启用异常值过滤
        self.outlier_threshold = 10.0  # MAD阈值（Modified Z-score），提高到10使其更严格，避免误判正常波动
        self.min_data_for_filter = 20  # 至少需要20个数据点才开始统计过滤
        self.recent_values = deque(maxlen=100)  # 增加窗口大小以提高稳定性
        self.outlier_count = 0  # 被过滤的异常值计数
        
        # 🔧 单点脉冲检测缓冲区（简化为滑动窗口）
        self.spike_buffer = deque(maxlen=5)  # 存储 (time, value)
        
        # 🔧 线程安全锁
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
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.setContentsMargins(5, 5, 5, 5)

        self.text_area.setMinimumHeight(40)
        self.text_area.setMaximumHeight(100)

        top_row.addWidget(output_group, stretch=1)

        # 右侧小图
        self.small_fig = Figure(figsize=(5, 3), dpi=100)
        self.small_ax = self.small_fig.add_subplot(111)
        self.small_ax.set_title('最近 20s', fontsize=10)
        self.small_ax.set_xlabel('秒', fontsize=9)
        self.small_ax.set_ylabel('ADC', fontsize=9)
        self.small_ax.grid(True, which='major', alpha=0.3, linestyle='-', linewidth=0.6)
        self.small_ax.set_facecolor('#ffffff')
        self.small_line, = self.small_ax.plot([], [], 'r-', linewidth=1.2, antialiased=True)
        self.small_canvas = FigureCanvas(self.small_fig)
        top_row.addWidget(self.small_canvas, stretch=2)

        right_layout.addLayout(top_row)

        # 下部：实时波形图
        plot_group = QGroupBox("实时波形图")
        plot_layout = QHBoxLayout()
        plot_layout.setContentsMargins(5, 2, 5, 5)

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
    
    def is_outlier_mad(self, value):
        """
        🔧 基于多点邻域的异常值检测
        检查当前值是否为孤立的异常点（与前后都不连续）
        如果连续多个点都在新的数值范围，说明是正常漂移，不是异常
        返回: (is_outlier, replacement_value)
        """
        if not self.enable_outlier_filter:
            return False, value
        
        # 第一层：过滤明显的极端值（硬件错误/溢出）
        if abs(value) > 8000000:
            replacement = self.recent_values[-1] if len(self.recent_values) > 0 else 0
            return True, replacement
        
        # 数据不足时不进行统计过滤
        if len(self.recent_values) < self.min_data_for_filter:
            return False, value
        
        # 🔧 关键改进：只检查最近的几个点（3-5个），而不是很多点
        # 这样可以快速适应数据漂移，而不会被旧数据影响
        recent_count = min(5, len(self.recent_values))
        recent_neighbors = list(self.recent_values)[-recent_count:]
        
        # 计算与最近邻点的差异
        recent_diffs = [abs(value - v) for v in recent_neighbors]
        min_recent_diff = min(recent_diffs)  # 与最近点的最小差异
        
        # 计算最近邻点之间的正常波动
        if len(recent_neighbors) >= 2:
            neighbor_diffs = [abs(recent_neighbors[i] - recent_neighbors[i-1]) 
                            for i in range(1, len(recent_neighbors))]
            typical_diff = sum(neighbor_diffs) / len(neighbor_diffs) if neighbor_diffs else 0
            max_neighbor_diff = max(neighbor_diffs) if neighbor_diffs else 0
        else:
            typical_diff = 0
            max_neighbor_diff = 0
        
        # 🔧 核心判断：只有当前值与**最近的几个点**都差异很大时，才是异常
        # 如果与最近点接近，说明是数据漂移的延续，不是异常
        
        # 动态阈值：基于最近邻点的波动情况
        # 如果邻点波动大，阈值也相应提高
        dynamic_threshold = max(5000, 30 * typical_diff, 3 * max_neighbor_diff)
        
        # 判断条件：
        # 与最近点的差异必须远超正常波动，才认为是异常
        is_outlier = min_recent_diff > dynamic_threshold
        
        if is_outlier:
            # 使用最近点的中位数作为替换值
            replacement = sorted(recent_neighbors)[len(recent_neighbors)//2]
            return True, int(replacement)
        
        return False, value
    
    def detect_spike(self, prev_v, curr_v, next_v):
        """
        🔧 基于趋势的单点脉冲检测
        只检测真正孤立的单点突变，必须满足：
        1. 与前后两点都差异巨大
        2. 前后两点彼此接近（稳定状态）
        3. 差异是数量级级别的（不是小波动）
        返回: (is_spike, replacement_value)
        """
        if not self.enable_outlier_filter:
            return False, curr_v
            
        try:
            # 计算三点之间的差异
            diff_to_prev = abs(curr_v - prev_v)
            diff_to_next = abs(curr_v - next_v)
            diff_between_neighbors = abs(next_v - prev_v)
            
            # 🔧 关键：判断是否与前后点接近
            # 使用绝对阈值，而不是相对阈值（避免在大数值时误判）
            closeness_threshold = 5000  # 差异小于5000认为是接近的
            
            # 如果与前点或后点接近，不是脉冲
            if diff_to_prev < closeness_threshold or diff_to_next < closeness_threshold:
                return False, curr_v
            
            # 如果前后点差异也很大，说明数据在剧烈变化，不是单点脉冲
            if diff_between_neighbors > closeness_threshold:
                return False, curr_v
            
            # 🔧 只有满足以下所有条件，才是真正的单点脉冲：
            # 1. 与前后点都差异很大（至少10000）
            # 2. 中点偏差远大于前后点间差异（至少20倍）
            # 3. 前后点彼此接近（差异小于5000）
            
            spike_threshold = 10000  # 脉冲的最小幅度
            ratio_threshold = 20.0   # 中点偏差与邻点差异的倍数
            
            interp = (prev_v + next_v) / 2.0
            curr_dev = abs(curr_v - interp)
            
            is_large_spike = (diff_to_prev > spike_threshold) and (diff_to_next > spike_threshold)
            is_extreme_ratio = curr_dev > (ratio_threshold * max(diff_between_neighbors, 1))
            neighbors_stable = diff_between_neighbors < closeness_threshold
            
            if is_large_spike and is_extreme_ratio and neighbors_stable:
                return True, int(interp)
                
        except Exception:
            pass
            
        return False, curr_v
        
    def extract_and_plot_adc(self, line):
        """从串口数据中提取ADC值并更新图形"""
        # 匹配 RAW ADC: 后面的数值
        match = re.search(r'RAW ADC:\s*(-?\d+)', line)
        if match:
            try:
                raw = int(match.group(1))
                
                # 转换为有符号值
                if raw < 0:
                    signed = raw
                else:
                    if raw & 0x800000:
                        signed = raw - 0x1000000
                    else:
                        signed = raw

                current_time = time.time() - self.start_time
                
                # 🔧 使用线程锁保护数据处理
                with self.data_lock:
                    # 🔧 第一步：MAD统计异常值检测
                    is_outlier, replacement = self.is_outlier_mad(signed)
                    
                    if is_outlier:
                        self.outlier_count += 1
                        self.update_filter_stats()
                        self.log_message(f"⚠️ 统计异常值: {signed} -> {replacement} (共过滤 {self.outlier_count} 个)")
                        value_to_buffer = replacement
                    else:
                        value_to_buffer = signed
                    
                    # 🔧 第二步：添加到脉冲检测缓冲区
                    self.spike_buffer.append((current_time, value_to_buffer))
                    
                    # 🔧 第三步：当缓冲区有至少3个点时，检测中间点是否为脉冲
                    if len(self.spike_buffer) >= 3:
                        # 取中间点
                        t1, v1 = self.spike_buffer[-2]
                        v0 = self.spike_buffer[-3][1]
                        v2 = self.spike_buffer[-1][1]
                        
                        # 检测脉冲
                        is_spike, spike_replacement = self.detect_spike(v0, v1, v2)
                        
                        if is_spike:
                            self.outlier_count += 1
                            self.update_filter_stats()
                            self.log_message(f"⚠️ 单点脉冲: {v1} -> {spike_replacement} (共过滤 {self.outlier_count} 个)")
                            final_value = spike_replacement
                        else:
                            final_value = v1
                        
                        # 添加到绘图数据
                        self.recent_values.append(final_value)
                        self.plot_data_x.append(t1)
                        self.plot_data_y.append(final_value)
                    
                    # 🔧 初始阶段：缓冲区不足3个点时，不添加到绘图
                    # 这样可以确保所有绘图数据都经过了完整的异常值检测
                
                # 限制绘图频率
                now = time.time()
                if now - self.last_draw_time >= self.draw_interval:
                    self.update_plot()
                    self.last_draw_time = now
                    
            except ValueError:
                pass
                
    def update_plot(self):
        """更新图形显示"""
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
            
            # 数据抽样
            if len(display_x) > 500:
                step = len(display_x) // 500
                display_x = display_x[::step]
                display_y = display_y[::step]
            
            self.line.set_data(display_x, display_y)
            
            # X轴范围设置
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
            
            # 平滑过渡
            if self.current_y_min is None or self.current_y_max is None:
                self.current_y_min = suggested_y_min
                self.current_y_max = suggested_y_max
            else:
                expand_alpha = 0.5
                shrink_alpha = 0.1
                
                if suggested_y_min < self.current_y_min:
                    self.current_y_min = self.current_y_min * (1 - expand_alpha) + suggested_y_min * expand_alpha
                else:
                    self.current_y_min = self.current_y_min * (1 - shrink_alpha) + suggested_y_min * shrink_alpha
                
                if suggested_y_max > self.current_y_max:
                    self.current_y_max = self.current_y_max * (1 - expand_alpha) + suggested_y_max * expand_alpha
                else:
                    self.current_y_max = self.current_y_max * (1 - shrink_alpha) + suggested_y_max * shrink_alpha
            
            self.ax.set_ylim(self.current_y_min, self.current_y_max)

            # 自动调整刻度
            self.auto_adjust_ticks(x_range, y_range, len(display_x))

            # 小图：显示最近20秒
            try:
                small_time_threshold = current_time - 20.0
                small_indices = [i for i, t in enumerate(x_data) if t >= small_time_threshold]
                small_x = [x_data[i] for i in small_indices]
                small_y = [y_data[i] for i in small_indices]

                if small_x and small_y:
                    if len(small_x) > 200:
                        step2 = len(small_x) // 200
                        small_x = small_x[::step2]
                        small_y = small_y[::step2]

                    self.small_line.set_data(small_x, small_y)
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

                self.small_canvas.draw_idle()
            except Exception:
                pass

            self.canvas.draw_idle()
        except Exception as e:
            try:
                self.log_message(f"绘图错误: {str(e)}\n")
            except Exception:
                print(f"绘图错误: {e}")
            
    def auto_adjust_ticks(self, x_range, y_range, data_count):
        """根据数据范围和密度智能调整刻度间距"""
        
        # X轴刻度
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
        
        # Y轴刻度
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
        
        self.ax.yaxis.set_major_locator(MaxNLocator(nbins=y_ticks, integer=False, prune='both'))
        self.ax.yaxis.set_minor_locator(AutoMinorLocator(y_minor_divs))
        
        # 网格线
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
            formatter = ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((0, 0))
            self.ax.yaxis.set_major_formatter(formatter)
        elif y_max_abs > 10000:
            def format_with_commas(x, pos):
                return f'{int(x):,}'
            self.ax.yaxis.set_major_formatter(FuncFormatter(format_with_commas))
        else:
            self.ax.yaxis.set_major_formatter(ScalarFormatter())
        
    def clear_plot(self):
        """清除图形数据"""
        with self.data_lock:
            self.plot_data_x.clear()
            self.plot_data_y.clear()
            self.spike_buffer.clear()
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

            self.canvas.draw()
        
    def reset_time(self):
        """重置时间起点"""
        self.start_time = time.time()
        self.last_draw_time = 0
        self.clear_plot()
    
    def toggle_filter(self, state):
        """切换异常值过滤功能"""
        self.enable_outlier_filter = (state == 2)
        status = "已启用" if self.enable_outlier_filter else "已禁用"
        self.log_message(f"异常值过滤功能 {status}\n")
        
    def update_filter_stats(self):
        """更新过滤统计信息"""
        if hasattr(self, 'filter_stats_label'):
            self.filter_stats_label.setText(f"已过滤: {self.outlier_count} 个异常值")
            
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
