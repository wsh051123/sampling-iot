/*
 * ===================================================================================
 * CS1237 Arduino 驱动代码
 * 修正点：1.参考电压匹配VDD 2.建立时间合规 3.满幅数据误判 4.通道选择功能 5.Power down模式 6.时序冗余优化
 * 日期: 2025-11-17
 * ===================================================================================
 */

// ========== 核心配置（用户需根据硬件修改） ==========
#define VDD 5.0f          // 实际供电电压（5V或3.3V，需与硬件一致）
#define DEFAULT_CHANNEL 0 // 默认通道：0=通道A，1=保留，2=温度，3=内短

// ========== 引脚定义 ==========
const int CS1237_SCLK = 5;
const int CS1237_DOUT_DRDY = 4;

// ========== 全局变量 ==========
float pga_gain = 128.0f;
int sample_rate_code = 0;       // 0:10Hz, 1:40Hz, 2:640Hz, 3:1280Hz
int current_channel = DEFAULT_CHANNEL; // 当前通道
uint8_t cs1237_config = 0x0C;   // 默认配置：PGA=128(0x0C)+10Hz(0x00)+通道A(0x00) → 0b00001100=0x0C
float vref = VDD;               // 参考电压=VDD（内部基准模式，手册P8/P9）

// ========== CS1237 命令字 (手册P16) ==========
#define CS1237_CMD_WRITE_CONFIG 0x65 // 写配置寄存器命令
#define CS1237_CMD_READ_CONFIG  0x56 // 读配置寄存器命令

// ========== 配置寄存器位定义 (手册P17，完整修正) ==========
#define CS1237_PGA_MASK    0x0C    // PGA选择位：B3-B2
#define CS1237_PGA_1       0x00    // PGA=1
#define CS1237_PGA_2       0x04    // PGA=2
#define CS1237_PGA_64      0x08    // PGA=64
#define CS1237_PGA_128     0x0C    // PGA=128（默认）

#define CS1237_SPEED_MASK  0x30    // 采样率选择位：B5-B4
#define CS1237_SPEED_10HZ  0x00    // 10Hz（默认）
#define CS1237_SPEED_40HZ  0x10    // 40Hz
#define CS1237_SPEED_640HZ 0x20    // 640Hz
#define CS1237_SPEED_1280HZ 0x30   // 1280Hz

#define CS1237_CH_MASK     0x03    // 通道选择位：B1-B0（新增）
#define CS1237_CH_A        0x00    // 通道A（默认）
#define CS1237_CH_RESERVED 0x01    // 保留（手册P17）
#define CS1237_CH_TEMP     0x02    // 温度传感器（需PGA=1）
#define CS1237_CH_SHORT    0x03    // 内短模式

#define CS1237_REFO_OFF    0x40    // 基准输出关闭位：B6

// ========== 通讯协议定义 ==========
const byte FRAME_HEAD_1 = 0xAA;
const byte FRAME_HEAD_2 = 0x55;
const byte FRAME_TAIL_1 = 0x0D;
const byte FRAME_TAIL_2 = 0x0A;
const byte CMD_ADC_DATA = 0x01;
const byte CMD_ERROR = 0x03;
const byte CMD_STATUS = 0x04;
const byte CMD_SET_PGA = 0xA1;
const byte CMD_SET_RATE = 0xA2;
const byte CMD_SET_CHANNEL = 0xA3; // 新增：通道配置确认
const byte CMD_POWER_DOWN = 0xA4;  // 新增：Power down确认
const byte CMD_CONFIG_ACK = 0xB1;
const byte ERR_SPI_READ = 0x01;
const byte ERR_DATA_INVALID = 0x02;
const byte ERR_TIMEOUT = 0x03;
const byte ERR_TEMP_PGA = 0x04;    // 新增：测温模式PGA错误

// ========== 统计信息 ==========
unsigned long totalReads = 0;
unsigned long successfulReads = 0;
unsigned long errorCount = 0;

// =================================================================
// ========== 函数原型 (前向声明) ==========
// =================================================================
void processCommand(char command);
byte calculateChecksum(byte* data, int len);
void sendADCFrame(long adcValue);
void sendErrorFrame(byte errorCode);
void sendStatusFrame();
void sendConfigAck(byte configType, byte value);
void readAndDisplayData();
void continuousRead();
void configurationMode();
void setPGAMenu();
void setSampleRateMenu();
void setChannelMenu();           // 新增：通道配置菜单
void enterPowerDownMode();       // 新增：进入Power down
void exitPowerDownMode();        // 新增：退出Power down
void quickSetPGA();
void quickSetRate();
void quickSetChannel();          // 新增：快速通道配置
void printCurrentConfig();
void showHelp();
void clockCycle();
bool waitForChipReady(unsigned long timeout_ms = 500);
void setPGAHardware(int pga_code);
void setSampleRateHardware(int rate_code);
void setChannelHardware(int ch_code); // 新增：通道硬件配置
void initCS1237();
void parseConfig(uint8_t config);
bool writeCS1237Config(uint8_t config);
uint8_t readCS1237Register();
long readCS1237ADC();
float convertADCToVoltage(long adcValue); // 新增：ADC值转电压
float convertADCToTemp(long adcValue, float calibTemp = 25.0f, long calibCode = 0); // 新增：ADC值转温度

// =================================================================
// ========== 初始化与主循环 ==========
// =================================================================
void setup() {
  Serial.begin(115200);
  pinMode(CS1237_SCLK, OUTPUT);
  pinMode(CS1237_DOUT_DRDY, INPUT);
  digitalWrite(CS1237_SCLK, LOW);
  
  delay(500); // 上电稳定时间
  initCS1237();
  
  Serial.println(F("\nCS1237 ADC - Firmware Rev 1.1 Compliant (修正版)"));
  Serial.print(F("当前供电电压配置: ")); Serial.print(VDD); Serial.println(F("V"));
  printCurrentConfig();
  showHelp();
  sendStatusFrame();
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    while (Serial.available()) Serial.read(); // 清空缓冲区
    processCommand(command);
  }
}

// =================================================================
// ========== 命令处理与协议帧函数 ==========
// =================================================================
void processCommand(char command) {
  switch (command) {
    case 'R': case 'r': readAndDisplayData(); break;
    case 'A': case 'a': continuousRead(); break;
    case 'C': case 'c': configurationMode(); break;
    case 'S': case 's': printCurrentConfig(); sendStatusFrame(); break;
    case 'P': case 'p': quickSetPGA(); break;
    case 'F': case 'f': quickSetRate(); break;
    case 'H': case 'h': quickSetChannel(); break; // 新增：快速通道配置
    case 'D': case 'd': enterPowerDownMode(); break; // 新增：进入Power down
    case 'U': case 'u': exitPowerDownMode(); break;  // 新增：退出Power down
    default: if (command != '\n' && command != '\r') { showHelp(); }
  }
}

byte calculateChecksum(byte* data, int len) {
  byte checksum = 0;
  for (int i = 0; i < len; i++) { checksum ^= data[i]; }
  return checksum;
}

void sendADCFrame(long adcValue) {
  byte frame[11];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x05; // 长度: 命令(1) + 数据(4)
  frame[idx++] = CMD_ADC_DATA;
  frame[idx++] = (adcValue >> 24) & 0xFF;
  frame[idx++] = (adcValue >> 16) & 0xFF;
  frame[idx++] = (adcValue >> 8) & 0xFF;
  frame[idx++] = adcValue & 0xFF;
  frame[idx++] = calculateChecksum(&frame[2], 6);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
  
   //额外打印：电压/温度值（方便调试）
  Serial.print(F("ADC原始值: 0x")); Serial.println(adcValue, HEX);
  if (current_channel == 2) { // 温度通道
  float temp = convertADCToTemp(adcValue);
  Serial.print(F("计算温度: ")); Serial.print(temp); Serial.println(F("℃ (未校准，需单点校正)"));
  } else { // 电压通道
    float voltage = convertADCToVoltage(adcValue);
    Serial.print(F("计算电压: ")); Serial.print(voltage, 6); Serial.println(F("V"));
  }
}

void sendErrorFrame(byte errorCode) {
  byte frame[8];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x02; // 长度
  frame[idx++] = CMD_ERROR;
  frame[idx++] = errorCode;
  frame[idx++] = calculateChecksum(&frame[2], 3);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
  errorCount++;
  
  // 打印错误描述
  Serial.print(F("错误: "));
  switch(errorCode) {
    case ERR_SPI_READ: Serial.println(F("SPI读取失败")); break;
    case ERR_DATA_INVALID: Serial.println(F("数据无效（已废弃该判断）")); break;
    case ERR_TIMEOUT: Serial.println(F("芯片响应超时")); break;
    case ERR_TEMP_PGA: Serial.println(F("测温模式需设置PGA=1")); break;
    default: Serial.println(F("未知错误"));
  }
}

void sendStatusFrame() {
  byte frame[12];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x06; // 长度：命令(1)+数据(5)
  frame[idx++] = CMD_STATUS;
  // PGA编码
  byte pga_code = (pga_gain == 1.0f) ? 0 : (pga_gain == 2.0f) ? 1 : (pga_gain == 64.0f) ? 2 : 3;
  frame[idx++] = pga_code;
  // 采样率编码
  frame[idx++] = sample_rate_code;
  // 通道编码
  frame[idx++] = current_channel;
  // 成功读取次数（2字节）
  frame[idx++] = (successfulReads >> 16) & 0xFF;
  frame[idx++] = successfulReads & 0xFF;
  // 校验和
  frame[idx++] = calculateChecksum(&frame[2], 7);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
}

void sendConfigAck(byte configType, byte value) {
  byte frame[9];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x03; // 长度
  frame[idx++] = CMD_CONFIG_ACK;
  frame[idx++] = configType;
  frame[idx++] = value;
  frame[idx++] = calculateChecksum(&frame[2], 4);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
  
  // 打印确认信息
  Serial.print(F("配置确认: "));
  switch(configType) {
    case CMD_SET_PGA: Serial.print(F("PGA=")); Serial.println(pga_gain); break;
    case CMD_SET_RATE: Serial.print(F("采样率=")); Serial.print(value == 0 ? 10 : value == 1 ? 40 : value == 2 ? 640 : 1280); Serial.println(F("Hz")); break;
    case CMD_SET_CHANNEL: Serial.print(F("通道=")); Serial.println(value == 0 ? "A" : value == 1 ? "保留" : value == 2 ? "温度" : "内短"); break;
    case CMD_POWER_DOWN: Serial.println(value == 1 ? "已进入Power down" : "已退出Power down"); break;
    default: Serial.println(F("未知配置"));
  }
}

// =================================================================
// ========== 数据读取与显示 ==========
// =================================================================
void readAndDisplayData() {
  totalReads++;
  // 检查Power down状态（SCLK高电平则退出）
  if (digitalRead(CS1237_SCLK) == HIGH) {
    exitPowerDownMode();
    delay(10); // 退出后稳定时间
  }
  
  long adcValue = readCS1237ADC();
  if (adcValue == -1) {
    sendErrorFrame(ERR_TIMEOUT);
    return;
  }
  
  // 移除满幅数据误判（手册P14：0x7FFFFF/0x800000为正常满幅值）
  successfulReads++;
  
  // 转换为32位有符号数（24位补码扩展）
  if (adcValue & 0x800000) {
    adcValue |= 0xFF000000;
  }
  
  sendADCFrame(adcValue);
}

void continuousRead() {
  Serial.println(F("\n开始连续读取... 发送 'S' 停止"));
  while (true) {
    if (Serial.available() > 0) {
      char stopChar = Serial.read();
      if (stopChar == 's' || stopChar == 'S') {
        Serial.println(F("停止连续读取"));
        sendStatusFrame();
        break;
      }
    }
    readAndDisplayData();
    // 延迟：避免数据刷屏过快（可根据采样率调整）
    delay(sample_rate_code <= 1 ? 100 : 10);
  }
}

// =================================================================
// ========== 配置菜单函数 ==========
// =================================================================
void configurationMode() {
  Serial.println(F("\n=== CS1237 配置模式 ==="));
  Serial.println(F("1. 设置 PGA 增益"));
  Serial.println(F("2. 设置 采样率"));
  Serial.println(F("3. 设置 通道"));
  Serial.println(F("4. 进入 Power down 模式"));
  Serial.println(F("5. 退出 Power down 模式"));
  Serial.println(F("6. 返回主菜单"));
  Serial.print(F("请输入选择 [1-6]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 30000) { // 30秒超时
      Serial.println(F("\n超时，返回主菜单"));
      return;
    }
  }
  
  char choice = Serial.read();
  while (Serial.available()) Serial.read(); // 清空缓冲区
  
  switch (choice) {
    case '1': setPGAMenu(); break;
    case '2': setSampleRateMenu(); break;
    case '3': setChannelMenu(); break;
    case '4': enterPowerDownMode(); break;
    case '5': exitPowerDownMode(); break;
    case '6': return;
    default: Serial.println(F("无效选择")); return;
  }
  
  printCurrentConfig();
  sendStatusFrame();
}

void setPGAMenu() {
  Serial.println(F("\n--- PGA 增益设置 ---"));
  Serial.println(F("0: PGA = 1"));
  Serial.println(F("1: PGA = 2"));
  Serial.println(F("2: PGA = 64"));
  Serial.println(F("3: PGA = 128"));
  Serial.print(F("请选择 PGA [0-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 15000) { // 15秒超时
      Serial.println(F("\n超时，返回配置菜单"));
      return;
    }
  }
  
  char c = Serial.read();
  while (Serial.available()) Serial.read();
  if (c >= '0' && c <= '3') {
    setPGAHardware(c - '0');
  } else {
    Serial.println(F("无效输入"));
  }
}

void setSampleRateMenu() {
  Serial.println(F("\n--- 采样率设置 ---"));
  Serial.println(F("0: 10 Hz"));
  Serial.println(F("1: 40 Hz"));
  Serial.println(F("2: 640 Hz"));
  Serial.println(F("3: 1280 Hz"));
  Serial.print(F("请选择采样率 [0-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 15000) { // 15秒超时
      Serial.println(F("\n超时，返回配置菜单"));
      return;
    }
  }
  
  char c = Serial.read();
  while (Serial.available()) Serial.read();
  if (c >= '0' && c <= '3') {
    setSampleRateHardware(c - '0');
  } else {
    Serial.println(F("无效输入"));
  }
}

void setChannelMenu() {
  Serial.println(F("\n--- 通道设置 ---"));
  Serial.println(F("0: 通道A（差分输入）"));
  Serial.println(F("1: 保留（不推荐使用）"));
  Serial.println(F("2: 温度传感器（需PGA=1）"));
  Serial.println(F("3: 内短模式（自检用）"));
  Serial.print(F("请选择通道 [0-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 15000) { // 15秒超时
      Serial.println(F("\n超时，返回配置菜单"));
      return;
    }
  }
  
  char c = Serial.read();
  while (Serial.available()) Serial.read();
  if (c >= '0' && c <= '3') {
    setChannelHardware(c - '0');
  } else {
    Serial.println(F("无效输入"));
  }
}

void enterPowerDownMode() {
  Serial.print(F("\n进入 Power down 模式... "));
  // 手册P18：SCLK高电平保持>100µs
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(150); // 冗余设计，确保>100µs
  Serial.println(F("完成"));
  sendConfigAck(CMD_POWER_DOWN, 1); // 1=进入
}

void exitPowerDownMode() {
  Serial.print(F("\n退出 Power down 模式... "));
  // 手册P18：SCLK拉低，需保持>10µs
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(20); // 冗余设计，确保>10µs
  // 退出后需等待建立时间（手册P13）
  float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
  int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
  delay(delayMs);
  Serial.println(F("完成"));
  sendConfigAck(CMD_POWER_DOWN, 0); // 0=退出
}

// 快速配置函数
void quickSetPGA() { setPGAMenu(); }
void quickSetRate() { setSampleRateMenu(); }
void quickSetChannel() { setChannelMenu(); }

// =================================================================
// ========== 配置显示与帮助 ==========
// =================================================================
void printCurrentConfig() {
  Serial.println(F("\n=== 当前 CS1237 配置 ==="));
  // PGA增益
  Serial.print(F("1. PGA 增益: x")); Serial.println(pga_gain);
  // 采样率
  Serial.print(F("2. 采样率: "));
  switch(sample_rate_code) {
    case 0: Serial.println(F("10 Hz")); break;
    case 1: Serial.println(F("40 Hz")); break;
    case 2: Serial.println(F("640 Hz")); break;
    case 3: Serial.println(F("1280 Hz")); break;
  }
  // 通道
  Serial.print(F("3. 当前通道: "));
  switch(current_channel) {
    case 0: Serial.println(F("通道A（差分输入）")); break;
    case 1: Serial.println(F("保留（不推荐）")); break;
    case 2: Serial.println(F("温度传感器（需PGA=1）")); break;
    case 3: Serial.println(F("内短模式（自检）")); break;
  }
  // 配置寄存器
  Serial.print(F("4. 配置寄存器 (Hex): 0x")); Serial.println(cs1237_config, HEX);
  // 参考电压
  Serial.print(F("5. 参考电压: ")); Serial.print(vref); Serial.println(F("V（内部基准）"));
  // 统计信息
  Serial.print(F("6. 统计: 总读取=")); Serial.print(totalReads);
  Serial.print(F(" 成功=")); Serial.print(successfulReads);
  Serial.print(F(" 错误=")); Serial.println(errorCount);
  Serial.println(F("-------------------------------------"));
}

void showHelp() {
  Serial.println(F("\n可用命令列表:"));
  Serial.println(F("  R/r - 单次读取ADC数据"));
  Serial.println(F("  A/a - 连续读取（发送'S'停止）"));
  Serial.println(F("  C/c - 进入配置模式（PGA/采样率/通道/Power down）"));
  Serial.println(F("  S/s - 显示当前配置并发送状态帧"));
  Serial.println(F("  P/p - 快速设置PGA增益"));
  Serial.println(F("  F/f - 快速设置采样率"));
  Serial.println(F("  H/h - 快速设置通道"));
  Serial.println(F("  D/d - 直接进入Power down模式"));
  Serial.println(F("  U/u - 直接退出Power down模式"));
}

// =================================================================
// ========== CS1237 底层硬件驱动 ==========
// =================================================================
void clockCycle() {
  // 手册P15：SCLK高低电平脉宽≥455ns，此处2µs冗余设计
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
}

bool waitForChipReady(unsigned long timeout_ms) {
  unsigned long start = millis();
  // DRDY/DOUT拉低表示数据就绪（手册P14）
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - start > timeout_ms) {
      return false; // 超时
    }
  }
  return true; // 就绪
}

void setPGAHardware(int pga_code) {
  uint8_t pga_bits;
  switch(pga_code) {
    case 0: pga_bits = CS1237_PGA_1;   pga_gain = 1.0f;   break;
    case 1: pga_bits = CS1237_PGA_2;   pga_gain = 2.0f;   break;
    case 2: pga_bits = CS1237_PGA_64;  pga_gain = 64.0f;  break;
    case 3: pga_bits = CS1237_PGA_128; pga_gain = 128.0f; break;
    default: return;
  }
  
  // 更新配置寄存器（掩码清除旧值，或上新值）
  cs1237_config = (cs1237_config & ~CS1237_PGA_MASK) | pga_bits;
  
  Serial.print(F("\n写入PGA配置 (0x"));
  Serial.print(cs1237_config, HEX);
  Serial.print(F(")... "));

  if (writeCS1237Config(cs1237_config)) {
    Serial.print(F("等待芯片就绪... "));
    if (waitForChipReady()) {
      // 手册P13：PGA切换后需建立时间
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      Serial.print(F("验证配置... "));
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_PGA, pga_code);
      } else {
        Serial.print(F("失败！读取值: 0x"));
        Serial.println(verify, HEX);
        parseConfig(cs1237_config); // 回退到预设配置
      }
    } else {
      Serial.println(F("失败！芯片未就绪"));
    }
  } else {
    Serial.println(F("失败！配置写入失败"));
  }
}

void setSampleRateHardware(int rate_code) {
  uint8_t speed_bits;
  switch(rate_code) {
    case 0: speed_bits = CS1237_SPEED_10HZ;  break;
    case 1: speed_bits = CS1237_SPEED_40HZ;  break;
    case 2: speed_bits = CS1237_SPEED_640HZ; break;
    case 3: speed_bits = CS1237_SPEED_1280HZ;break;
    default: return;
  }
  
  sample_rate_code = rate_code;
  // 更新配置寄存器
  cs1237_config = (cs1237_config & ~CS1237_SPEED_MASK) | speed_bits;
  
  Serial.print(F("\n写入采样率配置 (0x"));
  Serial.print(cs1237_config, HEX);
  Serial.print(F(")... "));

  if (writeCS1237Config(cs1237_config)) {
    Serial.print(F("等待芯片就绪... "));
    if (waitForChipReady()) {
      // 手册P13：速率切换后需建立时间
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      Serial.print(F("验证配置... "));
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_RATE, rate_code);
      } else {
        Serial.print(F("失败！读取值: 0x"));
        Serial.println(verify, HEX);
        parseConfig(cs1237_config); // 回退到预设配置
      }
    } else {
      Serial.println(F("失败！芯片未就绪"));
    }
  } else {
    Serial.println(F("失败！配置写入失败"));
  }
}

void setChannelHardware(int ch_code) {
  // 手册P17：测温模式（ch_code=2）必须使用PGA=1
  if (ch_code == 2 && pga_gain != 1.0f) {
    Serial.println(F("\n错误：测温模式需设置PGA=1，自动切换PGA"));
    setPGAHardware(0); // 强制设置PGA=1
    delay(100); // 等待PGA切换完成
  }
  
  uint8_t ch_bits;
  switch(ch_code) {
    case 0: ch_bits = CS1237_CH_A;        break;
    case 1: ch_bits = CS1237_CH_RESERVED; break;
    case 2: ch_bits = CS1237_CH_TEMP;     break;
    case 3: ch_bits = CS1237_CH_SHORT;    break;
    default: return;
  }
  
  current_channel = ch_code;
  // 更新配置寄存器
  cs1237_config = (cs1237_config & ~CS1237_CH_MASK) | ch_bits;
  
  Serial.print(F("\n写入通道配置 (0x"));
  Serial.print(cs1237_config, HEX);
  Serial.print(F(")... "));

  if (writeCS1237Config(cs1237_config)) {
    Serial.print(F("等待芯片就绪... "));
    if (waitForChipReady()) {
      // 手册P13：通道切换后需建立时间（2ms基础+转换周期）
      delay(2); // t1≥2ms（手册P13）
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      Serial.print(F("验证配置... "));
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_CHANNEL, ch_code);
      } else {
        Serial.print(F("失败！读取值: 0x"));
        Serial.println(verify, HEX);
        parseConfig(cs1237_config); // 回退到预设配置
      }
    } else {
      Serial.println(F("失败！芯片未就绪"));
    }
  } else {
    Serial.println(F("失败！配置写入失败"));
  }
}

void initCS1237() {
  Serial.print(F("初始化 CS1237... "));
  uint8_t currentConfig = readCS1237Register();
  
  if (currentConfig != 0xFF) { // 读取成功（0xFF为超时/失败）
    cs1237_config = currentConfig;
    parseConfig(currentConfig);
    Serial.println(F("成功（读取现有配置）"));
  } else {
    Serial.println(F("读取失败，写入默认配置..."));
    // 写入默认配置：PGA=128+10Hz+通道A
    if (writeCS1237Config(cs1237_config)) {
      if (waitForChipReady()) {
        // 默认配置建立时间（10Hz速率，3个周期=300ms）
        delay(300);
        uint8_t verify = readCS1237Register();
        if (verify == cs1237_config) {
          Serial.println(F("默认配置写入成功"));
          parseConfig(verify);
        } else {
          Serial.println(F("致命错误：默认配置验证失败，请检查接线"));
        }
      } else {
        Serial.println(F("致命错误：写入后芯片无响应，请检查接线"));
      }
    } else {
      Serial.println(F("致命错误：默认配置写入失败，请检查接线"));
    }
  }
}

void parseConfig(uint8_t config) {
  // 解析PGA配置
  uint8_t pga_bits = config & CS1237_PGA_MASK;
  switch(pga_bits) {
    case CS1237_PGA_1:   pga_gain = 1.0f;   break;
    case CS1237_PGA_2:   pga_gain = 2.0f;   break;
    case CS1237_PGA_64:  pga_gain = 64.0f;  break;
    case CS1237_PGA_128: pga_gain = 128.0f; break;
  }
  
  // 解析采样率配置
  uint8_t speed_bits = config & CS1237_SPEED_MASK;
  switch(speed_bits) {
    case CS1237_SPEED_10HZ:   sample_rate_code = 0; break;
    case CS1237_SPEED_40HZ:   sample_rate_code = 1; break;
    case CS1237_SPEED_640HZ:  sample_rate_code = 2; break;
    case CS1237_SPEED_1280HZ: sample_rate_code = 3; break;
  }
  
  // 解析通道配置
  uint8_t ch_bits = config & CS1237_CH_MASK;
  switch(ch_bits) {
    case CS1237_CH_A:        current_channel = 0; break;
    case CS1237_CH_RESERVED: current_channel = 1; break;
    case CS1237_CH_TEMP:     current_channel = 2; break;
    case CS1237_CH_SHORT:    current_channel = 3; break;
  }
}

bool writeCS1237Config(uint8_t config) {
  // 等待芯片就绪
  if (!waitForChipReady()) return false;

  // 手册P16：配置写入时序（46个SCLK）
  // 步骤1：读取24位ADC数据（丢弃）
  for (int i = 0; i < 24; i++) clockCycle();
  // 步骤2：读取2位状态（update1/update2，丢弃）
  clockCycle();
  clockCycle();
  // 步骤3：第27个SCLK：DRDY/DOUT拉高
  pinMode(CS1237_DOUT_DRDY, OUTPUT);
  digitalWrite(CS1237_DOUT_DRDY, HIGH);
  clockCycle();
  // 步骤4：第28-29个SCLK：切换为输入（保持高电平）
  clockCycle();
  clockCycle();
  // 步骤5：第30-36个SCLK：写入7位命令字（0x65=写配置）
  for (int i = 6; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (CS1237_CMD_WRITE_CONFIG >> i) & 0x01);
    clockCycle();
  }
  // 步骤6：第37个SCLK：切换为输入（写配置时保持输入）
  clockCycle();
  // 步骤7：第38-45个SCLK：写入8位配置数据
  for (int i = 7; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (config >> i) & 0x01);
    clockCycle();
  }
  // 步骤8：第46个SCLK：切换为输出并拉高
  pinMode(CS1237_DOUT_DRDY, INPUT);
  clockCycle();
  
  digitalWrite(CS1237_SCLK, LOW);
  return true;
}

uint8_t readCS1237Register() {
  // 等待芯片就绪
  if (!waitForChipReady()) return 0xFF;

  uint8_t data = 0;
  // 手册P16：配置读取时序（46个SCLK）
  // 步骤1：读取24位ADC数据（丢弃）
  for (int i = 0; i < 24; i++) clockCycle();
  // 步骤2：读取2位状态（update1/update2，丢弃）
  clockCycle();
  clockCycle();
  // 步骤3：第27个SCLK：DRDY/DOUT拉高
  pinMode(CS1237_DOUT_DRDY, OUTPUT);
  digitalWrite(CS1237_DOUT_DRDY, HIGH);
  clockCycle();
  // 步骤4：第28-29个SCLK：切换为输入（保持高电平）
  clockCycle();
  clockCycle();
  // 步骤5：第30-36个SCLK：写入7位命令字（0x56=读配置）
  for (int i = 6; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (CS1237_CMD_READ_CONFIG >> i) & 0x01);
    clockCycle();
  }
  // 步骤6：第37个SCLK：切换为输出（读配置时切换为输出）
  pinMode(CS1237_DOUT_DRDY, INPUT);
  clockCycle();
  // 步骤7：第38-45个SCLK：读取8位配置数据
  for (int i = 0; i < 8; i++) {
    clockCycle();
    data = (data << 1) | digitalRead(CS1237_DOUT_DRDY);
  }
  // 步骤8：第46个SCLK：保持输出
  clockCycle();
  
  digitalWrite(CS1237_SCLK, LOW);
  // 移除冗余等待（原代码问题点6）
  return data;
}

long readCS1237ADC() {
  // 等待芯片就绪（超时200ms）
  if (!waitForChipReady(200)) return -1;

  long value = 0;
  // 手册P15：读取24位ADC数据（高位先出）
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(5); // 满足传输延迟t6≥455ns
    value = (value << 1) | digitalRead(CS1237_DOUT_DRDY);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(5); // 满足保持时间t7≥227.5ns
  }
  
  // 读取2位状态位（update1/update2，丢弃）
  clockCycle();
  clockCycle();
  
  return value;
}

// =================================================================
// ========== 数据转换工具函数 ==========
// =================================================================
float convertADCToVoltage(long adcValue) {
  // 手册P14：电压计算公式：VIN = (ADC值 * VREF) / (PGA * 2^23)
  // 注：2^23=8388608，ADC值为24位补码（已扩展为32位）
  const float scale = vref / (pga_gain * 8388608.0f);
  return (float)adcValue * scale;
}

float convertADCToTemp(long adcValue, float calibTemp, long calibCode) {
  // 手册P11：温度计算公式：T = Yb*(273.15+A)/Ya - 273.15
  // A=校准温度(℃)，Ya=校准温度码值，Yb=当前温度码值
  if (calibCode == 0) {
    // 未校准时返回相对值（需用户自行单点校准）
    return (float)adcValue * 0.01f - 50.0f; // 粗略估算（仅作参考）
  } else {
    return (float)adcValue * (273.15f + calibTemp) / (float)calibCode - 273.15f;
  }
}