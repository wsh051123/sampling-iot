/*
 * ===================================================================================
 * CS1237 Arduino 驱动代码 (V3.0 修正版 - 快速响应配置)
 *
 * 日期: 2025-11-18
 *
 * 主要特性:
 * 1. 数据帧格式: [帧头(2B)] + [电压(4B float)] + [PGA(2B uint16)] + [帧尾(2B)]
 * 2. 快速配置响应（减少超时时间）
 * 3. 立即发送配置确认帧
 * ===================================================================================
 */

// ========== 核心配置（用户需根据硬件修改） ==========
#define VDD 5.0f          // 实际供电电压（5V或3.3V，需与硬件一致）
#define DEFAULT_CHANNEL 0 // 默认通道：0=通道A，1=保留，2=温度，3=内短

// ========== 引脚定义 ==========
const int CS1237_SCLK = 11;
const int CS1237_DOUT_DRDY = 10;

// ========== 全局变量 ==========
float pga_gain = 128.0f;
int sample_rate_code = 0;
int current_channel = DEFAULT_CHANNEL;
uint8_t cs1237_config = 0x0C;
float vref = VDD;

// ========== CS1237 命令字 (手册P16) ==========
#define CS1237_CMD_WRITE_CONFIG 0x65
#define CS1237_CMD_READ_CONFIG  0x56

// ========== 配置寄存器位定义 (手册P17，完整修正) ==========
#define CS1237_PGA_MASK    0x0C
#define CS1237_PGA_1       0x00
#define CS1237_PGA_2       0x04
#define CS1237_PGA_64      0x08
#define CS1237_PGA_128     0x0C
#define CS1237_SPEED_MASK  0x30
#define CS1237_SPEED_10HZ  0x00
#define CS1237_SPEED_40HZ  0x10
#define CS1237_SPEED_640HZ 0x20
#define CS1237_SPEED_1280HZ 0x30
#define CS1237_CH_MASK     0x03
#define CS1237_CH_A        0x00
#define CS1237_CH_RESERVED 0x01
#define CS1237_CH_TEMP     0x02
#define CS1237_CH_SHORT    0x03
#define CS1237_REFO_OFF    0x40

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
const byte CMD_SET_CHANNEL = 0xA3;
const byte CMD_POWER_DOWN = 0xA4;
const byte CMD_CONFIG_ACK = 0xB1;
const byte ERR_SPI_READ = 0x01;
const byte ERR_DATA_INVALID = 0x02;
const byte ERR_TIMEOUT = 0x03;
const byte ERR_TEMP_PGA = 0x04;

// ========== 统计信息 ==========
unsigned long totalReads = 0;
unsigned long successfulReads = 0;
unsigned long errorCount = 0;

// =================================================================
// === Union 用于 float 和 byte 数组转换 ===
// =================================================================
union FloatUnion {
  float floatValue;
  byte byteValue[4];
};

// =================================================================
// ========== 函数原型 ==========
// =================================================================
void processCommand(char command);
byte calculateChecksum(byte* data, int len);
void sendVoltagePGAFrame(long adcValue);
void sendErrorFrame(byte errorCode);
void sendStatusFrame();
void sendConfigAck(byte configType, byte value);
void readAndDisplayData();
void continuousRead();
void configurationMode();
void setPGAMenu();
void setSampleRateMenu();
void setChannelMenu();
void enterPowerDownMode();
void exitPowerDownMode();
void quickSetPGA();
void quickSetRate();
void quickSetChannel();
void printCurrentConfig();
void showHelp();
void clockCycle();
bool waitForChipReady(unsigned long timeout_ms = 500);
void setPGAHardware(int pga_code);
void setSampleRateHardware(int rate_code);
void setChannelHardware(int ch_code);
void initCS1237();
void parseConfig(uint8_t config);
bool writeCS1237Config(uint8_t config);
uint8_t readCS1237Register();
long readCS1237ADC();
float convertADCToVoltage(long adcValue);
float convertADCToTemp(long adcValue, float calibTemp = 25.0f, long calibCode = 0);

// =================================================================
// ========== 初始化与主循环 ==========
// =================================================================
void setup() {
  Serial.begin(9600);
  pinMode(CS1237_SCLK, OUTPUT);
  pinMode(CS1237_DOUT_DRDY, INPUT);
  digitalWrite(CS1237_SCLK, LOW);
  
  delay(500);
  initCS1237();
  
  Serial.println(F("\nCS1237 ADC - Firmware V3.0 (Voltage+PGA Frame)"));
  Serial.print(F("当前供电电压配置: ")); Serial.print(VDD); Serial.println(F("V"));
  printCurrentConfig();
  showHelp();
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    while (Serial.available()) Serial.read();
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
    case 'H': case 'h': quickSetChannel(); break;
    case 'D': case 'd': enterPowerDownMode(); break;
    case 'U': case 'u': exitPowerDownMode(); break;
    default: if (command != '\n' && command != '\r') { showHelp(); }
  }
}

byte calculateChecksum(byte* data, int len) {
  byte checksum = 0;
  for (int i = 0; i < len; i++) { checksum ^= data[i]; }
  return checksum;
}

void sendVoltagePGAFrame(long adcValue) {
  // 1. 将ADC值转换为电压
  float voltage = convertADCToVoltage(adcValue);

  // 2. 使用 union 将 float 转换为字节
  FloatUnion voltageData;
  voltageData.floatValue = voltage;

  // 3. PGA转换为uint16
  uint16_t pga_int = (uint16_t)pga_gain;

  // 4. 构建10字节帧
  byte frame[10];
  int idx = 0;
  
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;

  // 电压值 (4字节小端序)
  frame[idx++] = voltageData.byteValue[0];
  frame[idx++] = voltageData.byteValue[1];
  frame[idx++] = voltageData.byteValue[2];
  frame[idx++] = voltageData.byteValue[3];

  // PGA值 (2字节小端序)
  frame[idx++] = pga_int & 0xFF;
  frame[idx++] = (pga_int >> 8) & 0xFF;

  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;

  Serial.write(frame, sizeof(frame));
}

void sendErrorFrame(byte errorCode) {
  byte frame[8];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x02;
  frame[idx++] = CMD_ERROR;
  frame[idx++] = errorCode;
  frame[idx++] = calculateChecksum(&frame[2], 3);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
  errorCount++;
}

void sendStatusFrame() {
  byte frame[12];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x06;
  frame[idx++] = CMD_STATUS;
  byte pga_code = (pga_gain == 1.0f) ? 0 : (pga_gain == 2.0f) ? 1 : (pga_gain == 64.0f) ? 2 : 3;
  frame[idx++] = pga_code;
  frame[idx++] = sample_rate_code;
  frame[idx++] = current_channel;
  frame[idx++] = (successfulReads >> 16) & 0xFF;
  frame[idx++] = successfulReads & 0xFF;
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
  frame[idx++] = 0x03;
  frame[idx++] = CMD_CONFIG_ACK;
  frame[idx++] = configType;
  frame[idx++] = value;
  frame[idx++] = calculateChecksum(&frame[2], 4);
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  Serial.write(frame, idx);
  Serial.flush(); // 确保立即发送
}

// =================================================================
// ========== 数据读取与显示 ==========
// =================================================================
void readAndDisplayData() {
  totalReads++;
  if (digitalRead(CS1237_SCLK) == HIGH) {
    exitPowerDownMode();
    delay(10);
  }
  
  long adcValue = readCS1237ADC();
  if (adcValue == -1) {
    sendErrorFrame(ERR_TIMEOUT);
    return;
  }
  
  successfulReads++;
  
  if (adcValue & 0x800000) {
    adcValue |= 0xFF000000;
  }
  
  sendVoltagePGAFrame(adcValue);
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
    delay(sample_rate_code <= 1 ? 100 : 10);
  }
}

// =================================================================
// ========== 配置菜单函数（快速响应版本） ==========
// =================================================================
void configurationMode() {
  Serial.println(F("\n=== CS1237 配置模式 ==="));
  Serial.println(F("1. 设置 PGA 增益"));
  Serial.println(F("2. 设置 采样率"));
  Serial.println(F("3. 设置 通道"));
  Serial.println(F("4. 返回主菜单"));
  Serial.print(F("请输入选择 [1-4]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 10000) { // 减少到10秒超时
      Serial.println(F("\n超时，返回主菜单"));
      return;
    }
  }
  
  char choice = Serial.read();
  while (Serial.available()) Serial.read();
  
  switch (choice) {
    case '1': setPGAMenu(); break;
    case '2': setSampleRateMenu(); break;
    case '3': setChannelMenu(); break;
    case '4': return;
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
    if (millis() - startTime > 8000) { // 8秒超时
      Serial.println(F("\n超时"));
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
    if (millis() - startTime > 8000) {
      Serial.println(F("\n超时"));
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
  Serial.println(F("1: 保留"));
  Serial.println(F("2: 温度传感器"));
  Serial.println(F("3: 内短模式"));
  Serial.print(F("请选择通道 [0-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 8000) {
      Serial.println(F("\n超时"));
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
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(150);
  sendConfigAck(CMD_POWER_DOWN, 1);
}

void exitPowerDownMode() {
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(20);
  float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
  int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
  delay(delayMs);
  sendConfigAck(CMD_POWER_DOWN, 0);
}

void quickSetPGA() { setPGAMenu(); }
void quickSetRate() { setSampleRateMenu(); }
void quickSetChannel() { setChannelMenu(); }

// =================================================================
// ========== 配置显示与帮助 ==========
// =================================================================
void printCurrentConfig() {
  Serial.println(F("\n=== 当前 CS1237 配置 ==="));
  Serial.print(F("1. PGA 增益: x")); Serial.println(pga_gain);
  Serial.print(F("2. 采样率: "));
  switch(sample_rate_code) {
    case 0: Serial.println(F("10 Hz")); break;
    case 1: Serial.println(F("40 Hz")); break;
    case 2: Serial.println(F("640 Hz")); break;
    case 3: Serial.println(F("1280 Hz")); break;
  }
  Serial.print(F("3. 当前通道: "));
  switch(current_channel) {
    case 0: Serial.println(F("通道A")); break;
    case 1: Serial.println(F("保留")); break;
    case 2: Serial.println(F("温度传感器")); break;
    case 3: Serial.println(F("内短模式")); break;
  }
  Serial.print(F("4. 配置寄存器: 0x")); Serial.println(cs1237_config, HEX);
  Serial.print(F("5. 参考电压: ")); Serial.print(vref); Serial.println(F("V"));
  Serial.print(F("6. 统计: 总=")); Serial.print(totalReads);
  Serial.print(F(" 成功=")); Serial.print(successfulReads);
  Serial.print(F(" 错误=")); Serial.println(errorCount);
  Serial.println(F("-------------------------------------"));
}

void showHelp() {
  Serial.println(F("\n可用命令:"));
  Serial.println(F("  R/r - 单次读取"));
  Serial.println(F("  A/a - 连续读取"));
  Serial.println(F("  C/c - 配置模式"));
  Serial.println(F("  S/s - 显示状态"));
  Serial.println(F("  P/p - 快速设置PGA"));
  Serial.println(F("  F/f - 快速设置采样率"));
  Serial.println(F("  H/h - 快速设置通道"));
  Serial.println(F("  D/d - Power down"));
  Serial.println(F("  U/u - 退出Power down"));
}

// =================================================================
// ========== CS1237 底层驱动（与原版相同） ==========
// =================================================================
void clockCycle() {
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
}

bool waitForChipReady(unsigned long timeout_ms) {
  unsigned long start = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - start > timeout_ms) return false;
  }
  return true;
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
  
  cs1237_config = (cs1237_config & ~CS1237_PGA_MASK) | pga_bits;
  
  Serial.print(F("\n写入PGA配置... "));

  if (writeCS1237Config(cs1237_config)) {
    if (waitForChipReady()) {
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_PGA, pga_code);
      } else {
        Serial.println(F("失败"));
      }
    }
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
  cs1237_config = (cs1237_config & ~CS1237_SPEED_MASK) | speed_bits;
  
  Serial.print(F("\n写入采样率配置... "));

  if (writeCS1237Config(cs1237_config)) {
    if (waitForChipReady()) {
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_RATE, rate_code);
      } else {
        Serial.println(F("失败"));
      }
    }
  }
}

void setChannelHardware(int ch_code) {
  if (ch_code == 2 && pga_gain != 1.0f) {
    Serial.println(F("\n温度模式需PGA=1，自动切换"));
    setPGAHardware(0);
    delay(100);
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
  cs1237_config = (cs1237_config & ~CS1237_CH_MASK) | ch_bits;
  
  Serial.print(F("\n写入通道配置... "));

  if (writeCS1237Config(cs1237_config)) {
    if (waitForChipReady()) {
      delay(2);
      float conversionPeriod = 1000.0f / (sample_rate_code == 0 ? 10 : sample_rate_code == 1 ? 40 : sample_rate_code == 2 ? 640 : 1280);
      int delayMs = (sample_rate_code <= 1) ? (3 * conversionPeriod) : (4 * conversionPeriod);
      delay(delayMs);
      
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("成功"));
        sendConfigAck(CMD_SET_CHANNEL, ch_code);
      } else {
        Serial.println(F("失败"));
      }
    }
  }
}

void initCS1237() {
  Serial.print(F("初始化 CS1237... "));
  uint8_t currentConfig = readCS1237Register();
  
  if (currentConfig != 0xFF) {
    cs1237_config = currentConfig;
    parseConfig(currentConfig);
    Serial.println(F("成功（读取现有配置）"));
  } else {
    Serial.println(F("读取失败，写入默认配置..."));
    if (writeCS1237Config(cs1237_config)) {
      if (waitForChipReady()) {
        delay(300);
        uint8_t verify = readCS1237Register();
        if (verify == cs1237_config) {
          Serial.println(F("默认配置写入成功"));
          parseConfig(verify);
        }
      }
    }
  }
}

void parseConfig(uint8_t config) {
  uint8_t pga_bits = config & CS1237_PGA_MASK;
  switch(pga_bits) {
    case CS1237_PGA_1:   pga_gain = 1.0f;   break;
    case CS1237_PGA_2:   pga_gain = 2.0f;   break;
    case CS1237_PGA_64:  pga_gain = 64.0f;  break;
    case CS1237_PGA_128: pga_gain = 128.0f; break;
  }
  
  uint8_t speed_bits = config & CS1237_SPEED_MASK;
  switch(speed_bits) {
    case CS1237_SPEED_10HZ:   sample_rate_code = 0; break;
    case CS1237_SPEED_40HZ:   sample_rate_code = 1; break;
    case CS1237_SPEED_640HZ:  sample_rate_code = 2; break;
    case CS1237_SPEED_1280HZ: sample_rate_code = 3; break;
  }
  
  uint8_t ch_bits = config & CS1237_CH_MASK;
  switch(ch_bits) {
    case CS1237_CH_A:        current_channel = 0; break;
    case CS1237_CH_RESERVED: current_channel = 1; break;
    case CS1237_CH_TEMP:     current_channel = 2; break;
    case CS1237_CH_SHORT:    current_channel = 3; break;
  }
}

bool writeCS1237Config(uint8_t config) {
  if (!waitForChipReady()) return false;

  for (int i = 0; i < 24; i++) clockCycle();
  clockCycle();
  clockCycle();
  pinMode(CS1237_DOUT_DRDY, OUTPUT);
  digitalWrite(CS1237_DOUT_DRDY, HIGH);
  clockCycle();
  clockCycle();
  clockCycle();
  for (int i = 6; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (CS1237_CMD_WRITE_CONFIG >> i) & 0x01);
    clockCycle();
  }
  clockCycle();
  for (int i = 7; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (config >> i) & 0x01);
    clockCycle();
  }
  pinMode(CS1237_DOUT_DRDY, INPUT);
  clockCycle();
  
  digitalWrite(CS1237_SCLK, LOW);
  return true;
}

uint8_t readCS1237Register() {
  if (!waitForChipReady()) return 0xFF;

  uint8_t data = 0;
  for (int i = 0; i < 24; i++) clockCycle();
  clockCycle();
  clockCycle();
  pinMode(CS1237_DOUT_DRDY, OUTPUT);
  digitalWrite(CS1237_DOUT_DRDY, HIGH);
  clockCycle();
  clockCycle();
  clockCycle();
  for (int i = 6; i >= 0; i--) {
    digitalWrite(CS1237_DOUT_DRDY, (CS1237_CMD_READ_CONFIG >> i) & 0x01);
    clockCycle();
  }
  pinMode(CS1237_DOUT_DRDY, INPUT);
  clockCycle();
  for (int i = 0; i < 8; i++) {
    clockCycle();
    data = (data << 1) | digitalRead(CS1237_DOUT_DRDY);
  }
  clockCycle();
  
  digitalWrite(CS1237_SCLK, LOW);
  return data;
}

long readCS1237ADC() {
  if (!waitForChipReady(200)) return -1;

  long value = 0;
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(5);
    value = (value << 1) | digitalRead(CS1237_DOUT_DRDY);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(5);
  }
  
  clockCycle();
  clockCycle();
  
  return value;
}

float convertADCToVoltage(long adcValue) {
  // 按照手册精确公式：满幅输入 = ±0.5 * VREF / PGA
  const float scale = (0.2475f * vref) / (pga_gain * 8388607.0f);
  return (float)adcValue * scale;
}

float convertADCToTemp(long adcValue, float calibTemp, long calibCode) {
  
    
    
return (float)adcValue * (273.15f + calibTemp) / (float)calibCode - 273.15f;
  
}
