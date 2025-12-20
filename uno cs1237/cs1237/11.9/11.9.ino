// 引脚定义
const int CS1237_SCLK = 5;
const int CS1237_DOUT_DRDY = 4;

// 全局变量
float pga_gain = 128.0;
int sample_rate = 0;
uint8_t cs1237_config = 0x0C; // 默认配置: PGA=128, 10Hz, 通道A
float vref = 2.5; // 参考电压，通常是2.5V

// CS1237寄存器地址和位定义
#define CS1237_REG_CONFIG  0x00  // 配置寄存器
#define CS1237_REG_OFFSET  0x02  // 偏移寄存器

// 配置寄存器位定义
#define CS1237_PGA_MASK    0x03  // PGA增益掩码 (bit 0-1)
#define CS1237_PGA_1       0x00  // PGA = 1
#define CS1237_PGA_2       0x01  // PGA = 2
#define CS1237_PGA_64      0x02  // PGA = 64
#define CS1237_PGA_128     0x03  // PGA = 128

#define CS1237_SPEED_MASK  0x30  // 采样率掩码 (bit 4-5)
#define CS1237_SPEED_10HZ  0x00  // 10 Hz
#define CS1237_SPEED_40HZ  0x10  // 40 Hz
#define CS1237_SPEED_640HZ 0x20  // 640 Hz
#define CS1237_SPEED_1280HZ 0x30 // 1280 Hz

#define CS1237_REFO_OFF    0x40  // 参考电压关闭 (bit 6)
#define CS1237_CH_A        0x00  // 通道A (bit 7)

// ========== 通讯协议定义 ==========
// 帧头/帧尾
const byte FRAME_HEAD_1 = 0xAA;
const byte FRAME_HEAD_2 = 0x55;
const byte FRAME_TAIL_1 = 0x0D;
const byte FRAME_TAIL_2 = 0x0A;

// 命令类型
const byte CMD_ADC_DATA = 0x01;      // ADC数据帧
const byte CMD_ERROR = 0x03;         // 错误报告
const byte CMD_STATUS = 0x04;        // 状态信息
const byte CMD_SET_PGA = 0xA1;       // 设置PGA
const byte CMD_SET_RATE = 0xA2;      // 设置采样率
const byte CMD_CONFIG_ACK = 0xB1;    // 配置确认

// 错误码
const byte ERR_SPI_READ = 0x01;      // SPI读取失败
const byte ERR_DATA_INVALID = 0x02;  // 数据无效
const byte ERR_TIMEOUT = 0x03;       // 超时

// 统计信息
unsigned long totalReads = 0;
unsigned long successfulReads = 0;
unsigned long errorCount = 0;

void setup() {
  Serial.begin(115200);
  pinMode(CS1237_SCLK, OUTPUT);
  pinMode(CS1237_DOUT_DRDY, INPUT);
  digitalWrite(CS1237_SCLK, LOW);
  
  // 等待芯片稳定
  delay(200);
  
  // 初始化CS1237配置
  initCS1237();
  
  // 发送启动信息（仍保留文本，便于调试）
  Serial.println(F("CS1237 ADC - Protocol Mode"));
  Serial.println(F("Binary protocol enabled"));
  
  // 发送状态帧表示启动成功
  sendStatusFrame();
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    while (Serial.available()) Serial.read();
    processCommand(command);
  }
  delay(50);
}

void processCommand(char command) {
  switch (command) {
    case 'R': case 'r':
      readAndDisplayData();
      break;
    case 'A': case 'a':
      continuousRead();
      break;
    case 'C': case 'c':
      configurationMode();
      break;
    case 'S': case 's':
      printCurrentConfig();
      break;
    case 'P': case 'p':
      // 快速设置PGA（P0-P3）
      quickSetPGA();
      break;
    case 'F': case 'f':
      // 快速设置采样率（F0-F3）
      quickSetRate();
      break;
    default:
      if (command != '\n' && command != '\r') {
        showHelp();
      }
  }
}

// ========== 协议发送函数 ==========

// 计算校验和（XOR）
byte calculateChecksum(byte* data, int len) {
  byte checksum = 0;
  for (int i = 0; i < len; i++) {
    checksum ^= data[i];
  }
  return checksum;
}

// 发送ADC数据帧
void sendADCFrame(long adcValue) {
  byte frame[13];
  int idx = 0;
  
  // 帧头
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  
  // 长度（4字节数据）
  frame[idx++] = 0x04;
  
  // 命令
  frame[idx++] = CMD_ADC_DATA;
  
  // 数据（24位ADC值，用4字节传输，高位补0）
  frame[idx++] = 0x00;  // 补位
  frame[idx++] = (adcValue >> 16) & 0xFF;
  frame[idx++] = (adcValue >> 8) & 0xFF;
  frame[idx++] = adcValue & 0xFF;
  
  // 校验和（从长度到数据的所有字节）
  byte checksum = calculateChecksum(&frame[2], 6);
  frame[idx++] = checksum;
  
  // 帧尾
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  
  // 发送
  Serial.write(frame, idx);
  Serial.flush();  // 确保数据发送完毕
}

// 发送错误帧
void sendErrorFrame(byte errorCode) {
  byte frame[9];
  int idx = 0;
  
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x01;  // 长度
  frame[idx++] = CMD_ERROR;
  frame[idx++] = errorCode;
  
  byte checksum = calculateChecksum(&frame[2], 3);
  frame[idx++] = checksum;
  
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  
  Serial.write(frame, idx);
  Serial.flush();
  
  errorCount++;
}

// 发送状态帧
void sendStatusFrame() {
  byte frame[14];
  int idx = 0;
  
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x06;  // 长度：6字节
  frame[idx++] = CMD_STATUS;
  
  // 数据：PGA(1) + 采样率(1) + 成功读取数(4)
  byte pga_code = 3;  // 默认128
  if (pga_gain == 1.0) pga_code = 0;
  else if (pga_gain == 2.0) pga_code = 1;
  else if (pga_gain == 64.0) pga_code = 2;
  
  frame[idx++] = pga_code;
  frame[idx++] = sample_rate;
  
  // 成功读取数（4字节，大端序）
  frame[idx++] = (successfulReads >> 24) & 0xFF;
  frame[idx++] = (successfulReads >> 16) & 0xFF;
  frame[idx++] = (successfulReads >> 8) & 0xFF;
  frame[idx++] = successfulReads & 0xFF;
  
  byte checksum = calculateChecksum(&frame[2], 8);
  frame[idx++] = checksum;
  
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  
  Serial.write(frame, idx);
  Serial.flush();
}

// 发送配置确认帧
void sendConfigAck(byte configType, byte value) {
  byte frame[10];
  int idx = 0;
  
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x02;  // 长度
  frame[idx++] = CMD_CONFIG_ACK;
  frame[idx++] = configType;  // 配置类型（PGA或采样率）
  frame[idx++] = value;        // 配置值
  
  byte checksum = calculateChecksum(&frame[2], 4);
  frame[idx++] = checksum;
  
  frame[idx++] = FRAME_TAIL_1;
  frame[idx++] = FRAME_TAIL_2;
  
  Serial.write(frame, idx);
  Serial.flush();
}

// ========== 原有函数改造 ==========

// 改进的读取函数（使用协议发送）
void readAndDisplayData() {
  totalReads++;
  long adcValue = readCS1237ADC();
  
  if (adcValue == -1) {
    // 发送错误帧
    sendErrorFrame(ERR_SPI_READ);
    return;
  }
  
  // 严格的数据验证
  if (adcValue > 0xFFFFFF) {
    sendErrorFrame(ERR_DATA_INVALID);
    return;
  }
  
  // 检查SPI错误特征（高12位全1且值很小）
  if ((adcValue & 0xFFF000) == 0xFFF000 && 
      (adcValue > 0xFFFF00 || adcValue < 0xFFF100)) {
    sendErrorFrame(ERR_SPI_READ);
    return;
  }
  
  // 转换为有符号24位
  long signedValue = adcValue;
  if (signedValue & 0x800000) {
    signedValue -= 0x1000000;
  }
  
  // 成功读取，发送数据帧
  successfulReads++;
  sendADCFrame(signedValue);
}

void continuousRead() {
  // 发送文本提示（可选，便于调试）
  Serial.println(F("Starting continuous reading..."));
  
  unsigned long lastReadTime = 0;
  const unsigned long readInterval = 100; // ms
  
  while (true) {
    // 检查停止命令
    if (Serial.available() > 0) {
      char stopChar = Serial.read();
      if (stopChar == 's' || stopChar == 'S') {
        Serial.println(F("Stopping continuous reading"));
        // 发送最终状态
        sendStatusFrame();
        break;
      }
    }
    
    // 控制读取频率
    if (millis() - lastReadTime >= readInterval) {
      readAndDisplayData();
      lastReadTime = millis();
    }
    
    delay(10);
  }
}

// 配置菜单保持不变
void configurationMode() {
  Serial.println(F("\n=== CS1237 Configuration Mode ==="));
  Serial.println(F("1. Set PGA Gain"));
  Serial.println(F("2. Set Sample Rate")); 
  Serial.println(F("3. Set Reference Voltage"));
  Serial.println(F("4. Back to main menu"));
  Serial.print(F("Enter your choice [1-4]: "));
  
  unsigned long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 30000) {
      Serial.println(F("\nConfiguration mode timeout"));
      return;
    }
  }
  
  char choice = Serial.read();
  while (Serial.available()) Serial.read();
  Serial.println();
  
  switch (choice) {
    case '1':
      setPGAMenu();
      break;
    case '2':
      setSampleRateMenu();
      break;
    case '3':
      setVrefMenu();
      break;
    case '4':
      Serial.println(F("Returning to main menu"));
      return;
    default:
      Serial.println(F("Invalid choice!"));
      return;
  }
  
  printCurrentConfig();
}

void setPGAMenu() {
  Serial.println(F("\n--- PGA Gain Setting ---"));
  Serial.println(F("0: PGA = 1"));
  Serial.println(F("1: PGA = 2")); 
  Serial.println(F("2: PGA = 64"));
  Serial.println(F("3: PGA = 128"));
  Serial.print(F("Select PGA [0-3]: "));
  
  while (Serial.available()) Serial.read();
  
  int pga = -1;
  unsigned long startTime = millis();
  
  while (pga < 0 || pga > 3) {
    if (millis() - startTime > 15000) {
      Serial.println(F("\nTimeout, returning to menu"));
      return;
    }
    
    if (Serial.available()) {
      char c = Serial.read();
      if (c >= '0' && c <= '3') {
        pga = c - '0';
        break;
      } else if (c != '\n' && c != '\r') {
        Serial.println(F("\nInvalid input! Please enter 0, 1, 2, or 3"));
        Serial.print(F("Select PGA [0-3]: "));
        while (Serial.available()) Serial.read();
      }
    }
    delay(50);
  }
  
  while (Serial.available()) Serial.read();
  Serial.println();
  
  setPGA(pga);
}

void setSampleRateMenu() {
  Serial.println(F("\n--- Sample Rate Setting ---"));
  Serial.println(F("0: 10 Hz (High precision)"));
  Serial.println(F("1: 40 Hz (Balanced)"));
  Serial.println(F("2: 640 Hz (High speed)"));
  Serial.println(F("3: 1280 Hz (Maximum speed)"));
  Serial.print(F("Select sample rate [0-3]: "));
  
  while (Serial.available()) Serial.read();
  
  int rate = -1;
  unsigned long startTime = millis();
  
  while (rate < 0 || rate > 3) {
    if (millis() - startTime > 15000) {
      Serial.println(F("\nTimeout, returning to menu"));
      return;
    }
    
    if (Serial.available()) {
      char c = Serial.read();
      if (c >= '0' && c <= '3') {
        rate = c - '0';
        break;
      } else if (c != '\n' && c != '\r') {
        Serial.println(F("\nInvalid input! Please enter 0, 1, 2, or 3"));
        Serial.print(F("Select sample rate [0-3]: "));
        while (Serial.available()) Serial.read();
      }
    }
    delay(50);
  }
  
  while (Serial.available()) Serial.read();
  Serial.println();
  
  setSampleRate(rate);
}

// 新增参考电压设置
void setVrefMenu() {
  Serial.println(F("\n--- Reference Voltage Setting ---"));
  Serial.println(F("1: 2.5V (Typical)"));
  Serial.println(F("2: 3.3V"));
  Serial.println(F("3: 5.0V"));
  Serial.print(F("Select reference voltage [1-3]: "));
  
  while (Serial.available()) Serial.read();
  
  int vref_sel = -1;
  unsigned long startTime = millis();
  
  while (vref_sel < 1 || vref_sel > 3) {
    if (millis() - startTime > 15000) {
      Serial.println(F("\nTimeout, returning to menu"));
      return;
    }
    
    if (Serial.available()) {
      char c = Serial.read();
      if (c >= '1' && c <= '3') {
        vref_sel = c - '0';
        break;
      } else if (c != '\n' && c != '\r') {
        Serial.println(F("\nInvalid input! Please enter 1, 2, or 3"));
        Serial.print(F("Select reference voltage [1-3]: "));
        while (Serial.available()) Serial.read();
      }
    }
    delay(50);
  }
  
  while (Serial.available()) Serial.read();
  Serial.println();
  
  switch(vref_sel) {
    case 1: vref = 2.5; break;
    case 2: vref = 3.3; break;
    case 3: vref = 5.0; break;
  }
  Serial.print(F("Reference voltage set to: "));
  Serial.print(vref);
  Serial.println(F(" V"));
}

void setPGA(int pga) {
  // 调用硬件级设置
  setPGAHardware(pga);
}

void setSampleRate(int rate) {
  // 调用硬件级设置
  setSampleRateHardware(rate);
}

void printCurrentConfig() {
  Serial.println(F("\n=== Current CS1237 Configuration ==="));
  Serial.print(F("PGA Gain: "));
  Serial.println(pga_gain);
  
  Serial.print(F("Sample Rate: "));
  switch(sample_rate) {
    case 0: Serial.println(F("10 Hz")); break;
    case 1: Serial.println(F("40 Hz")); break;
    case 2: Serial.println(F("640 Hz")); break;
    case 3: Serial.println(F("1280 Hz")); break;
  }
  
  Serial.print(F("Reference Voltage: "));
  Serial.print(vref);
  Serial.println(F(" V"));
  
  Serial.print(F("Total reads: "));
  Serial.println(totalReads);
  Serial.print(F("Successful reads: "));
  Serial.println(successfulReads);
  Serial.print(F("Error count: "));
  Serial.println(errorCount);
  
  Serial.println(F("====================================="));
  
  // 同时发送状态帧
  sendStatusFrame();
}

void showHelp() {
  Serial.println(F("\nAvailable commands:"));
  Serial.println(F("R - Single read"));
  Serial.println(F("A - Continuous read (send 's' to stop)"));
  Serial.println(F("C - Configuration mode"));
  Serial.println(F("S - Show current configuration"));
  Serial.println(F("P - Quick set PGA (P0=1, P1=2, P2=64, P3=128)"));
  Serial.println(F("F - Quick set sample rate (F0=10Hz, F1=40Hz, F2=640Hz, F3=1280Hz)"));
}

// ========== CS1237硬件配置函数 ==========

// 初始化CS1237芯片
void initCS1237() {
  // 读取当前配置
  uint8_t currentConfig = readCS1237Register();
  
  if (currentConfig != 0xFF) {
    cs1237_config = currentConfig;
    parseConfig(currentConfig);
    Serial.println(F("CS1237 initialized, current config read"));
  } else {
    // 如果读取失败，写入默认配置
    Serial.println(F("Reading config failed, writing default config..."));
    writeCS1237Config(cs1237_config);
    delay(100);
    
    // 验证写入
    uint8_t verify = readCS1237Register();
    if (verify == cs1237_config) {
      Serial.println(F("Default config written successfully"));
    } else {
      Serial.println(F("Warning: Config write verification failed"));
    }
  }
}

// 从配置字节解析PGA和采样率
void parseConfig(uint8_t config) {
  // 解析PGA
  uint8_t pga_bits = config & CS1237_PGA_MASK;
  switch(pga_bits) {
    case CS1237_PGA_1:   pga_gain = 1.0; break;
    case CS1237_PGA_2:   pga_gain = 2.0; break;
    case CS1237_PGA_64:  pga_gain = 64.0; break;
    case CS1237_PGA_128: pga_gain = 128.0; break;
  }
  
  // 解析采样率
  uint8_t speed_bits = config & CS1237_SPEED_MASK;
  switch(speed_bits) {
    case CS1237_SPEED_10HZ:   sample_rate = 0; break;
    case CS1237_SPEED_40HZ:   sample_rate = 1; break;
    case CS1237_SPEED_640HZ:  sample_rate = 2; break;
    case CS1237_SPEED_1280HZ: sample_rate = 3; break;
  }
}

// 读取CS1237配置寄存器
uint8_t readCS1237Register() {
  // 等待数据就绪
  unsigned long timeout = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - timeout > 200) {
      return 0xFF;  // 超时返回错误
    }
    delayMicroseconds(10);
  }
  
  // 读取ADC数据（24个时钟）
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 发送额外时钟（1个时钟结束数据读取）
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
  
  // 发送25个时钟进入读配置寄存器模式（总共26个额外时钟）
  for (int i = 0; i < 25; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 等待DRDY变低（表示准备好读取）
  timeout = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - timeout > 500) {
      return 0xFF;
    }
    delayMicroseconds(100);
  }
  
  delayMicroseconds(100); // 额外稳定时间
  
  // 读取8位配置数据
  uint8_t config = 0;
  for (int i = 0; i < 8; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    
    int bitValue = digitalRead(CS1237_DOUT_DRDY);
    config = (config << 1) | bitValue;
    
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 额外一个时钟结束读取
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
  
  delay(50);  // 等待芯片稳定
  
  return config;
}

// 写入CS1237配置寄存器
bool writeCS1237Config(uint8_t config) {
  // 等待数据就绪
  unsigned long timeout = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - timeout > 200) {
      return false;
    }
    delayMicroseconds(10);
  }
  
  // 读取ADC数据（24个时钟）
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 额外一个时钟结束数据读取
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
  
  // 发送28个时钟进入写配置寄存器模式（总共29个额外时钟）
  for (int i = 0; i < 28; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 等待DRDY变低
  timeout = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - timeout > 500) {
      return false;
    }
    delayMicroseconds(100);
  }
  
  delayMicroseconds(100); // 额外稳定时间
  
  // 写入8位配置数据（从高位到低位）
  for (int i = 7; i >= 0; i--) {
    // 设置数据线为输出模式来写入数据
    pinMode(CS1237_DOUT_DRDY, OUTPUT);
    
    // 写入位
    if (config & (1 << i)) {
      digitalWrite(CS1237_DOUT_DRDY, HIGH);
    } else {
      digitalWrite(CS1237_DOUT_DRDY, LOW);
    }
    
    delayMicroseconds(2);
    
    // 时钟脉冲
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(2);
  }
  
  // 恢复DOUT/DRDY为输入模式
  pinMode(CS1237_DOUT_DRDY, INPUT);
  
  // 额外一个时钟结束写入
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
  
  delay(400);  // 等待芯片稳定（配置生效需要时间）
  
  return true;
}

// 快速设置PGA
void quickSetPGA() {
  Serial.print(F("Enter PGA value (0=1, 1=2, 2=64, 3=128): "));
  
  unsigned long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 10000) {
      Serial.println(F("\nTimeout"));
      return;
    }
  }
  
  char c = Serial.read();
  while (Serial.available()) Serial.read();
  Serial.println(c);
  
  if (c >= '0' && c <= '3') {
    setPGAHardware(c - '0');
  } else {
    Serial.println(F("Invalid input!"));
  }
}

// 快速设置采样率
void quickSetRate() {
  Serial.print(F("Enter rate (0=10Hz, 1=40Hz, 2=640Hz, 3=1280Hz): "));
  
  unsigned long startTime = millis();
  while (!Serial.available()) {
    if (millis() - startTime > 10000) {
      Serial.println(F("\nTimeout"));
      return;
    }
  }
  
  char c = Serial.read();
  while (Serial.available()) Serial.read();
  Serial.println(c);
  
  if (c >= '0' && c <= '3') {
    setSampleRateHardware(c - '0');
  } else {
    Serial.println(F("Invalid input!"));
  }
}

// 硬件级设置PGA
void setPGAHardware(int pga) {
  uint8_t pga_bits;
  
  switch(pga) {
    case 0: 
      pga_bits = CS1237_PGA_1;
      pga_gain = 1.0;
      break;
    case 1:
      pga_bits = CS1237_PGA_2;
      pga_gain = 2.0;
      break;
    case 2:
      pga_bits = CS1237_PGA_64;
      pga_gain = 64.0;
      break;
    case 3:
      pga_bits = CS1237_PGA_128;
      pga_gain = 128.0;
      break;
    default:
      return;
  }
  
  // 更新配置字节（保留其他位，只修改PGA位）
  cs1237_config = (cs1237_config & ~CS1237_PGA_MASK) | pga_bits;
  
  // 写入硬件
  Serial.print(F("Writing PGA config to hardware..."));
  if (writeCS1237Config(cs1237_config)) {
    // 验证写入
    uint8_t verify = readCS1237Register();
    if ((verify & CS1237_PGA_MASK) == pga_bits) {
      Serial.println(F(" Success!"));
      Serial.print(F("PGA gain set to: "));
      Serial.println(pga_gain);
      sendConfigAck(CMD_SET_PGA, pga);
    } else {
      Serial.println(F(" Verification failed!"));
    }
  } else {
    Serial.println(F(" Failed!"));
  }
}

// 硬件级设置采样率
void setSampleRateHardware(int rate) {
  uint8_t speed_bits;
  
  switch(rate) {
    case 0:
      speed_bits = CS1237_SPEED_10HZ;
      sample_rate = 0;
      break;
    case 1:
      speed_bits = CS1237_SPEED_40HZ;
      sample_rate = 1;
      break;
    case 2:
      speed_bits = CS1237_SPEED_640HZ;
      sample_rate = 2;
      break;
    case 3:
      speed_bits = CS1237_SPEED_1280HZ;
      sample_rate = 3;
      break;
    default:
      return;
  }
  
  // 更新配置字节（保留其他位，只修改采样率位）
  cs1237_config = (cs1237_config & ~CS1237_SPEED_MASK) | speed_bits;
  
  // 写入硬件
  Serial.print(F("Writing sample rate config to hardware..."));
  if (writeCS1237Config(cs1237_config)) {
    // 验证写入
    uint8_t verify = readCS1237Register();
    if ((verify & CS1237_SPEED_MASK) == speed_bits) {
      Serial.println(F(" Success!"));
      Serial.print(F("Sample rate set to: "));
      switch(rate) {
        case 0: Serial.println(F("10 Hz")); break;
        case 1: Serial.println(F("40 Hz")); break;
        case 2: Serial.println(F("640 Hz")); break;
        case 3: Serial.println(F("1280 Hz")); break;
      }
      sendConfigAck(CMD_SET_RATE, rate);
    } else {
      Serial.println(F(" Verification failed!"));
    }
  } else {
    Serial.println(F(" Failed!"));
  }
}

// 改进的ADC读取函数（增强SPI错误检测）
long readCS1237ADC() {
  // 等待数据就绪（带超时）
  unsigned long timeout = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - timeout > 100) {
      return -1;  // 超时
    }
    delayMicroseconds(10);
  }
  
  // 确保信号稳定
  delayMicroseconds(50);
  if (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    return -1;
  }
  
  long value = 0;
  
  // 更精确的时序控制
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(5); // 增加到5us
    
    int bitValue = digitalRead(CS1237_DOUT_DRDY);
    value = (value << 1) | bitValue;
    
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(5); // 增加到5us
  }
  
  // 额外时钟脉冲
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(5);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(5);
  
  // 验证读取的数据
  if (value == 0 || value == 0xFFFFFF) {
    return -1;
  }
  
  return value;
}