import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial
import serial.tools.list_ports
import threading
import time
from datetime import datetime
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import re

# 设置matplotlib中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

class CS1237_GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CS1237 ADC Controller")
        self.root.geometry("900x700")
        
        # 串口相关变量
        self.serial_port = None
        self.is_connected = False
        self.is_continuous = False
        
        # 当前配置状态
        self.current_pga = 128.0
        self.current_sample_rate = "10 Hz"
        
        # 过滤标志：是否显示过程信息
        self.show_process_info = False
        
        # 绘图数据
        self.plot_data_x = deque(maxlen=100)  # 时间数据，最多保存100个点
        self.plot_data_y = deque(maxlen=100)  # ADC数据
        self.start_time = time.time()
        
        self.create_widgets()
        
    def create_widgets(self):
        # 串口连接框架
        conn_frame = ttk.LabelFrame(self.root, text="串口连接", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)
        
        # 串口选择
        ttk.Label(conn_frame, text="选择串口:").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(conn_frame, width=15)
        self.port_combo.grid(row=0, column=1, padx=5)
        
        ttk.Label(conn_frame, text="波特率:").grid(row=0, column=2, sticky="w", padx=(20,0))
        self.baud_combo = ttk.Combobox(conn_frame, width=10, values=["9600", "115200", "57600", "38400"])
        self.baud_combo.set("9600")
        self.baud_combo.grid(row=0, column=3, padx=5)
        
        self.refresh_btn = ttk.Button(conn_frame, text="刷新串口", command=self.refresh_ports)
        self.refresh_btn.grid(row=0, column=4, padx=5)
        
        self.connect_btn = ttk.Button(conn_frame, text="连接", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=5)
        
        # 数据读取框架
        read_frame = ttk.LabelFrame(self.root, text="数据读取", padding=10)
        read_frame.pack(fill="x", padx=10, pady=5)
        
        self.single_read_btn = ttk.Button(read_frame, text="单次读取", command=self.single_read)
        self.single_read_btn.pack(side="left", padx=5)
        
        self.continuous_btn = ttk.Button(read_frame, text="开始连续读取", command=self.toggle_continuous)
        self.continuous_btn.pack(side="left", padx=5)
        
        # 配置框架
        config_frame = ttk.LabelFrame(self.root, text="配置设置", padding=10)
        config_frame.pack(fill="x", padx=10, pady=5)
        
        # PGA设置
        ttk.Label(config_frame, text="PGA增益:").grid(row=0, column=0, sticky="w")
        self.pga_var = tk.StringVar(value="128")
        pga_combo = ttk.Combobox(config_frame, textvariable=self.pga_var, 
                                values=["1", "2", "64", "128"], width=10)
        pga_combo.grid(row=0, column=1, padx=5, sticky="w")
        ttk.Button(config_frame, text="设置PGA", command=self.set_pga).grid(row=0, column=2, padx=5)
        
        # 采样率设置
        ttk.Label(config_frame, text="采样率:").grid(row=1, column=0, sticky="w", pady=(10,0))
        self.sample_rate_var = tk.StringVar(value="10 Hz")
        rate_combo = ttk.Combobox(config_frame, textvariable=self.sample_rate_var,
                                 values=["10 Hz", "40 Hz", "640 Hz", "1280 Hz"], width=10)
        rate_combo.grid(row=1, column=1, padx=5, pady=(10,0), sticky="w")
        ttk.Button(config_frame, text="设置采样率", command=self.set_sample_rate).grid(row=1, column=2, padx=5, pady=(10,0))
        
        # 状态查询
        ttk.Button(config_frame, text="查询状态", command=self.get_status).grid(row=2, column=0, columnspan=3, pady=(10,0))
        
        # 绘图区域
        plot_frame = ttk.LabelFrame(self.root, text="实时波形图", padding=10)
        plot_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # 创建matplotlib图形
        self.fig = Figure(figsize=(8, 3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel('时间 (秒)', fontsize=10)
        self.ax.set_ylabel('ADC 值', fontsize=10)
        self.ax.set_title('CS1237 ADC 实时数据', fontsize=11, fontweight='bold')
        self.ax.grid(True, which='major', alpha=0.4, linestyle='-')
        self.ax.grid(True, which='minor', alpha=0.15, linestyle=':')
        
        # 初始化空白图线
        self.line, = self.ax.plot([], [], 'b-', linewidth=1.5)
        
        # 创建画布
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        
        # 绘图控制按钮
        plot_btn_frame = ttk.Frame(plot_frame)
        plot_btn_frame.pack(fill="x", pady=(5,0))
        ttk.Button(plot_btn_frame, text="清除图形", command=self.clear_plot).pack(side="left", padx=5)
        ttk.Button(plot_btn_frame, text="重置时间", command=self.reset_time).pack(side="left", padx=5)
        
        # 数据显示区域
        display_frame = ttk.LabelFrame(self.root, text="数据输出", padding=10)
        display_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.text_area = scrolledtext.ScrolledText(display_frame, height=8, width=70, font=("Consolas", 9))
        self.text_area.pack(fill="both", expand=True)
        
        # 添加清除按钮
        btn_frame = ttk.Frame(display_frame)
        btn_frame.pack(fill="x", pady=(5,0))
        ttk.Button(btn_frame, text="清除输出", command=self.clear_output).pack(side="left")
        ttk.Button(btn_frame, text="发送帮助命令", command=self.show_help_cmd).pack(side="left", padx=5)
        
        # 状态栏
        self.status_var = tk.StringVar(value="就绪 - 请选择串口并连接")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken")
        status_bar.pack(fill="x", padx=10, pady=5)
        
        # 初始化串口列表
        self.refresh_ports()
        
    def refresh_ports(self):
        """刷新可用的串口列表"""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports:
            self.port_combo.set(ports[0])
            
    def toggle_connection(self):
        """连接/断开串口"""
        if not self.is_connected:
            self.connect_serial()
        else:
            self.disconnect_serial()
            
    def connect_serial(self):
        """连接串口"""
        try:
            port = self.port_combo.get()
            baud = int(self.baud_combo.get())
            
            if not port:
                messagebox.showerror("错误", "请选择串口")
                return
                
            self.serial_port = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # 等待Arduino重启
            
            self.is_connected = True
            self.connect_btn.config(text="断开")
            self.status_var.set(f"已连接: {port} @ {baud} baud")
            
            # 清空可能残留的数据
            if self.serial_port.in_waiting > 0:
                self.serial_port.reset_input_buffer()
            
            # 启动串口数据读取线程
            self.serial_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.serial_thread.start()
            
        except Exception as e:
            messagebox.showerror("连接错误", f"无法连接串口: {str(e)}")
            
    def disconnect_serial(self):
        """断开串口连接"""
        if self.serial_port and self.serial_port.is_open:
            # 停止连续读取
            if self.is_continuous:
                self.send_command('s')
                self.is_continuous = False
                self.continuous_btn.config(text="开始连续读取")
                
            self.serial_port.close()
            
        self.is_connected = False
        self.connect_btn.config(text="连接")
        self.status_var.set("已断开连接")
        
    def send_command(self, command, delay=0.05):
        """发送命令到Arduino"""
        if self.serial_port and self.serial_port.is_open:
            try:
                # 只发送单个字符,不加换行符(Arduino使用Serial.read()读取单字符)
                self.serial_port.write(command.encode())
                time.sleep(delay)  # 给Arduino处理命令的时间
                return True
            except Exception as e:
                self.log_message(f"发送命令错误: {str(e)}\n")
                return False
        else:
            messagebox.showwarning("警告", "串口未连接")
            return False
            
    def read_serial(self):
        """在单独线程中读取串口数据"""
        while self.is_connected and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting > 0:
                    line = self.serial_port.readline().decode().strip()
                    if line:
                        # 过滤不需要显示的信息
                        if self.should_display_line(line):
                            self.root.after(0, self.log_message, line + "\n")
                        
                        # 提取ADC数据并更新图形
                        self.extract_and_plot_adc(line)
           
            except Exception as e:
                if self.is_connected:  # 只在仍然连接时报告错误
                    self.root.after(0, self.log_message, f"读取错误: {str(e)}\n")
                break
            time.sleep(0.01)
            
    def should_display_line(self, line):
        """判断是否应该显示该行信息"""
        # 过滤掉的信息（Arduino的提示和菜单）
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
        
        # 如果包含过滤关键词，不显示
        for keyword in filter_keywords:
            if keyword in line:
                return False
        
        return True
    
    def log_message(self, message):
        """在文本区域显示消息"""
        self.text_area.insert(tk.END, message)
        self.text_area.see(tk.END)
        
    def clear_output(self):
        """清除输出区域"""
        self.text_area.delete(1.0, tk.END)
        
    def show_help_cmd(self):
        """显示Arduino帮助信息"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
        # 发送一个无效字符触发帮助
        if self.send_command('?'):
            pass  # 不显示过程信息
        
    def single_read(self):
        """单次读取数据"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
        self.send_command('R')
            
    def toggle_continuous(self):
        """切换连续读取模式"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
            
        if not self.is_continuous:
            if self.send_command('A'):
                self.is_continuous = True
                self.continuous_btn.config(text="停止连续读取")
        else:
            if self.send_command('s'):
                self.is_continuous = False
                self.continuous_btn.config(text="开始连续读取")
                
    def set_pga(self):
        """设置PGA增益"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
            
        pga_map = {"1": "0", "2": "1", "64": "2", "128": "3"}
        pga_value = self.pga_var.get()
        
        if pga_value in pga_map:
            # 进入配置模式
            if self.send_command('C', delay=0.2):
                # 选择PGA设置选项
                if self.send_command('1', delay=0.2):
                    # 发送PGA值
                    if self.send_command(pga_map[pga_value], delay=0.2):
                        self.current_pga = float(pga_value)
        else:
            messagebox.showwarning("警告", "请选择有效的PGA值")
            
    def set_sample_rate(self):
        """设置采样率"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
            
        rate_map = {"10 Hz": "0", "40 Hz": "1", "640 Hz": "2", "1280 Hz": "3"}
        rate_value = self.sample_rate_var.get()
        
        if rate_value in rate_map:
            # 进入配置模式
            if self.send_command('C', delay=0.2):
                # 选择采样率设置选项
                if self.send_command('2', delay=0.2):
                    # 发送采样率值
                    if self.send_command(rate_map[rate_value], delay=0.2):
                        self.current_sample_rate = rate_value
        else:
            messagebox.showwarning("警告", "请选择有效的采样率")
            
    def get_status(self):
        """查询当前配置状态"""
        if not self.is_connected:
            messagebox.showwarning("警告", "请先连接串口")
            return
        self.send_command('S')
    
    def extract_and_plot_adc(self, line):
        """从串口数据中提取ADC值并更新图形"""
        # 匹配 "RAW ADC: 数字" 格式
        match = re.search(r'RAW ADC:\s*(-?\d+)', line)
        if match:
            try:
                adc_value = int(match.group(1))
                current_time = time.time() - self.start_time
                
                # 添加数据到队列
                self.plot_data_x.append(current_time)
                self.plot_data_y.append(adc_value)
                
                # 在主线程中更新图形
                self.root.after(0, self.update_plot)
            except ValueError:
                pass
    
    def update_plot(self):
        """更新图形显示"""
        if len(self.plot_data_x) > 0 and len(self.plot_data_y) > 0:
            # 更新数据
            self.line.set_data(list(self.plot_data_x), list(self.plot_data_y))
            
            # 获取数据范围
            x_data = list(self.plot_data_x)
            y_data = list(self.plot_data_y)
            
            # X轴（时间）范围设置
            x_min, x_max = min(x_data), max(x_data)
            x_range = x_max - x_min
            
            # 添加5%的边距使图形更美观
            if x_range > 0:
                x_margin = x_range * 0.05
                self.ax.set_xlim(x_min - x_margin, x_max + x_margin)
            else:
                # 如果只有一个点，设置固定范围
                self.ax.set_xlim(x_min - 1, x_min + 1)
            
            # Y轴（ADC值）范围设置
            y_min, y_max = min(y_data), max(y_data)
            y_range = y_max - y_min
            
            if y_range > 0:
                # 添加10%的边距，确保数据点不会紧贴边缘
                y_margin = y_range * 0.1
                self.ax.set_ylim(y_min - y_margin, y_max + y_margin)
            else:
                # 如果所有值相同，设置合适的显示范围
                if y_min == 0:
                    self.ax.set_ylim(-100, 100)
                else:
                    self.ax.set_ylim(y_min - abs(y_min) * 0.1, y_max + abs(y_max) * 0.1)
            
            # 自动调整刻度密度
            self.auto_adjust_ticks(x_range, y_range)
            
            # 重新绘制
            self.canvas.draw_idle()
    
    def auto_adjust_ticks(self, x_range, y_range):
        """根据数据范围自动调整刻度间距"""
        from matplotlib.ticker import MaxNLocator, AutoMinorLocator
        
        # X轴刻度设置（时间）
        # 根据时间范围动态调整刻度数量
        if x_range < 10:
            x_ticks = 6  # 短时间范围，少量刻度
        elif x_range < 60:
            x_ticks = 8
        else:
            x_ticks = 10  # 长时间范围，更多刻度
        
        self.ax.xaxis.set_major_locator(MaxNLocator(nbins=x_ticks, integer=False))
        
        # Y轴刻度设置（ADC值）
        # 根据数值范围动态调整
        if y_range < 100:
            y_ticks = 8
        elif y_range < 1000:
            y_ticks = 10
        else:
            y_ticks = 8
        
        self.ax.yaxis.set_major_locator(MaxNLocator(nbins=y_ticks, integer=True))
        
        # 添加次要刻度线，使图形更精细
        self.ax.xaxis.set_minor_locator(AutoMinorLocator(2))
        self.ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        
        # 启用次要网格线
        self.ax.grid(True, which='major', alpha=0.4, linestyle='-')
        self.ax.grid(True, which='minor', alpha=0.15, linestyle=':')
    
    def clear_plot(self):
        """清除图形数据"""
        self.plot_data_x.clear()
        self.plot_data_y.clear()
        self.line.set_data([], [])
        
        # 重置坐标轴范围到初始状态
        self.ax.set_xlim(0, 10)
        self.ax.set_ylim(-100, 100)
        
        # 重置网格
        self.ax.grid(True, which='major', alpha=0.4, linestyle='-')
        self.ax.grid(True, which='minor', alpha=0.15, linestyle=':')
        
        self.canvas.draw()
    
    def reset_time(self):
        """重置时间起点"""
        self.start_time = time.time()
        self.clear_plot()

def main():
    root = tk.Tk()
    app = CS1237_GUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()