/*
 * ===================================================================================
 * CS1237 Arduino 驱动代码 (最终编译修正版)
 *
 * 日期: 2025-11-16
 *
 * 修订内容:
 * 1. 添加了所有函数的原型 (前向声明)，以解决 "not declared in this scope" 编译错误。
 * 2. 核心驱动逻辑保持不变，严格遵循 CS1237 Rev 1.1 手册规范。
 * ===================================================================================
 */

// ========== 引脚定义 ==========
const int CS1237_SCLK = 5;
const int CS1237_DOUT_DRDY = 4;

// ========== 全局变量 ==========
float pga_gain = 128.0;
int sample_rate_code = 0; // 0:10Hz, 1:40Hz, 2:640Hz, 3:1280Hz
uint8_t cs1237_config = 0x0C; // 默认配置: PGA=128 (0x0C), 10Hz (0x00), 通道A -> 0b00001100 = 0x0C
float vref = 2.5;

// ========== CS1237 命令字 (手册P16) ==========
#define CS1237_CMD_WRITE_CONFIG 0x65 // 写配置寄存器命令
#define CS1237_CMD_READ_CONFIG  0x56 // 读配置寄存器命令

// ========== 配置寄存器位定义 (手册P17, 已修正) ==========
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
const byte CMD_CONFIG_ACK = 0xB1;
const byte ERR_SPI_READ = 0x01;
const byte ERR_DATA_INVALID = 0x02;
const byte ERR_TIMEOUT = 0x03;

// ========== 统计信息 ==========
unsigned long totalReads = 0;
unsigned long successfulReads = 0;
unsigned long errorCount = 0;

// =================================================================
// ========== 函数原型 (前向声明) - 解决编译错误 ==========
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
void quickSetPGA();
void quickSetRate();
void printCurrentConfig();
void showHelp();
void clockCycle();
bool waitForChipReady(unsigned long timeout_ms = 500);
void setPGAHardware(int pga_code);
void setSampleRateHardware(int rate_code);
void initCS1237();
void parseConfig(uint8_t config);
bool writeCS1237Config(uint8_t config);
uint8_t readCS1237Register();
long readCS1237ADC();
// =================================================================

void setup() {
  Serial.begin(115200);
  pinMode(CS1237_SCLK, OUTPUT);
  pinMode(CS1237_DOUT_DRDY, INPUT);
  digitalWrite(CS1237_SCLK, LOW);
  
  delay(500); // 增加上电稳定时间
  
  initCS1237();
  
  Serial.println(F("\nCS1237 ADC - Protocol Mode (Firmware Rev 1.1 Compliant)"));
  printCurrentConfig();
  showHelp();
  
  sendStatusFrame();
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    // 清空缓冲区
    while (Serial.available()) Serial.read();
    processCommand(command);
  }
}

// ========== 主要功能和协议函数 (定义) ==========

void processCommand(char command) {
  switch (command) {
    case 'R': case 'r': readAndDisplayData(); break;
    case 'A': case 'a': continuousRead(); break;
    case 'C': case 'c': configurationMode(); break;
    case 'S': case 's': printCurrentConfig(); break;
    case 'P': case 'p': quickSetPGA(); break;
    case 'F': case 'f': quickSetRate(); break;
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
}

void sendStatusFrame() {
  byte frame[11];
  int idx = 0;
  frame[idx++] = FRAME_HEAD_1;
  frame[idx++] = FRAME_HEAD_2;
  frame[idx++] = 0x05; // 长度
  frame[idx++] = CMD_STATUS;
  byte pga_code = 3;
  if (pga_gain == 1.0) pga_code = 0;
  else if (pga_gain == 2.0) pga_code = 1;
  else if (pga_gain == 64.0) pga_code = 2;
  frame[idx++] = pga_code;
  frame[idx++] = sample_rate_code;
  frame[idx++] = (successfulReads >> 16) & 0xFF; // 简化为2字节
  frame[idx++] = successfulReads & 0xFF;
  frame[idx++] = calculateChecksum(&frame[2], 6);
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
}

void readAndDisplayData() {
  totalReads++;
  long adcValue = readCS1237ADC();
  
  if (adcValue == -1) {
    sendErrorFrame(ERR_TIMEOUT);
    return;
  }
  
  if (adcValue == 0x7FFFFF || adcValue == 0x800000) {
    sendErrorFrame(ERR_DATA_INVALID);
    return;
  }
  
  successfulReads++;
  
  // 转换为32位有符号数
  if (adcValue & 0x800000) {
    adcValue |= 0xFF000000;
  }
  
  sendADCFrame(adcValue);
}

void continuousRead() {
  Serial.println(F("\nStarting continuous reading... Send 'S' to stop."));
  
  while (true) {
    if (Serial.available() > 0) {
      char stopChar = Serial.read();
      if (stopChar == 's' || stopChar == 'S') {
        Serial.println(F("Stopping continuous reading."));
        sendStatusFrame();
        break;
      }
    }
    readAndDisplayData();
    delay(50); // 稍微延迟，避免刷屏太快
  }
}

void configurationMode() {
  Serial.println(F("\n=== CS1237 Configuration Mode ==="));
  Serial.println(F("1. Set PGA Gain"));
  Serial.println(F("2. Set Sample Rate"));
  Serial.println(F("3. Back to main menu"));
  Serial.print(F("Enter your choice [1-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) { if (millis() - startTime > 30000) { Serial.println(F("\nTimeout.")); return; } }
  
  char choice = Serial.read();
  while (Serial.available()) Serial.read();
  
  switch (choice) {
    case '1': setPGAMenu(); break;
    case '2': setSampleRateMenu(); break;
    case '3': return;
    default: Serial.println(F("Invalid choice.")); return;
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
  
  long startTime = millis();
  while (!Serial.available()) { if (millis() - startTime > 15000) { Serial.println(F("\nTimeout.")); return; } }
  
  char c = Serial.read();
  if (c >= '0' && c <= '3') {
    setPGAHardware(c - '0');
  } else {
    Serial.println(F("Invalid input."));
  }
  while (Serial.available()) Serial.read();
}

void setSampleRateMenu() {
  Serial.println(F("\n--- Sample Rate Setting ---"));
  Serial.println(F("0: 10 Hz"));
  Serial.println(F("1: 40 Hz"));
  Serial.println(F("2: 640 Hz"));
  Serial.println(F("3: 1280 Hz"));
  Serial.print(F("Select sample rate [0-3]: "));
  
  long startTime = millis();
  while (!Serial.available()) { if (millis() - startTime > 15000) { Serial.println(F("\nTimeout.")); return; } }
  
  char c = Serial.read();
  if (c >= '0' && c <= '3') {
    setSampleRateHardware(c - '0');
  } else {
    Serial.println(F("Invalid input."));
  }
  while (Serial.available()) Serial.read();
}

void quickSetPGA() {
    setPGAMenu();
}

void quickSetRate() {
    setSampleRateMenu();
}

void printCurrentConfig() {
  Serial.println(F("\n=== Current CS1237 Configuration ==="));
  Serial.print(F("PGA Gain: x"));
  Serial.println(pga_gain);
  
  Serial.print(F("Sample Rate: "));
  switch(sample_rate_code) {
    case 0: Serial.println(F("10 Hz")); break;
    case 1: Serial.println(F("40 Hz")); break;
    case 2: Serial.println(F("640 Hz")); break;
    case 3: Serial.println(F("1280 Hz")); break;
  }
  
  Serial.print(F("Config Register (Hex): 0x"));
  Serial.println(cs1237_config, HEX);

  Serial.println(F("-------------------------------------"));
}

void showHelp() {
  Serial.println(F("\nAvailable commands:"));
  Serial.println(F("  R - Single read"));
  Serial.println(F("  A - Continuous read (send 'S' to stop)"));
  Serial.println(F("  C - Configuration mode"));
  Serial.println(F("  S - Show current configuration"));
  Serial.println(F("  P - Quick set PGA menu"));
  Serial.println(F("  F - Quick set sample rate menu"));
}

// ========== CS1237 底层硬件驱动 (定义) ==========

void clockCycle() {
  digitalWrite(CS1237_SCLK, HIGH);
  delayMicroseconds(2);
  digitalWrite(CS1237_SCLK, LOW);
  delayMicroseconds(2);
}

bool waitForChipReady(unsigned long timeout_ms) {
  unsigned long start = millis();
  while (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    if (millis() - start > timeout_ms) {
      return false; // Timed out
    }
  }
  return true; // Chip is ready
}

void setPGAHardware(int pga_code) {
  uint8_t pga_bits;
  switch(pga_code) {
    case 0: pga_bits = CS1237_PGA_1;   pga_gain = 1.0;   break;
    case 1: pga_bits = CS1237_PGA_2;   pga_gain = 2.0;   break;
    case 2: pga_bits = CS1237_PGA_64;  pga_gain = 64.0;  break;
    case 3: pga_bits = CS1237_PGA_128; pga_gain = 128.0; break;
    default: return;
  }
  
  cs1237_config = (cs1237_config & ~CS1237_PGA_MASK) | pga_bits;
  
  Serial.print(F("Writing new config (0x"));
  Serial.print(cs1237_config, HEX);
  Serial.print(F(")... "));

  if (writeCS1237Config(cs1237_config)) {
    Serial.print(F("Waiting for chip... "));
    if (waitForChipReady()) {
      delay(10);
      Serial.print(F("Ready. Verifying... "));
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("Success!"));
        sendConfigAck(CMD_SET_PGA, pga_code);
      } else {
        Serial.print(F("FAILED! Read back 0x"));
        Serial.println(verify, HEX);
        parseConfig(cs1237_config); 
      }
    } else {
        Serial.println(F("Chip did not become ready. Write may have failed."));
    }
  } else {
    Serial.println(F("Write FAILED!"));
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
  
  Serial.print(F("Writing new config (0x"));
  Serial.print(cs1237_config, HEX);
  Serial.print(F(")... "));

  if (writeCS1237Config(cs1237_config)) {
    Serial.print(F("Waiting for chip... "));
    if (waitForChipReady()) {
      delay(10);
      Serial.print(F("Ready. Verifying... "));
      uint8_t verify = readCS1237Register();
      if (verify == cs1237_config) {
        Serial.println(F("Success!"));
        sendConfigAck(CMD_SET_RATE, rate_code);
      } else {
        Serial.print(F("FAILED! Read back 0x"));
        Serial.println(verify, HEX);
        parseConfig(cs1237_config);
      }
    } else {
        Serial.println(F("Chip did not become ready. Write may have failed."));
    }
  } else {
    Serial.println(F("Write FAILED!"));
  }
}

void initCS1237() {
  Serial.print(F("Initializing CS1237... "));
  uint8_t currentConfig = readCS1237Register();
  
  if (currentConfig != 0xFF) {
    cs1237_config = currentConfig;
    parseConfig(currentConfig);
    Serial.println(F("Found existing config."));
  } else {
    Serial.println(F("Read failed. Writing default config..."));
    writeCS1237Config(cs1237_config);
    if (waitForChipReady()) {
        uint8_t verify = readCS1237Register();
        if (verify == cs1237_config) {
          Serial.println(F("Default config written successfully."));
        } else {
          Serial.println(F("FATAL: Config write verification failed. Check wiring."));
        }
    } else {
        Serial.println(F("FATAL: Chip unresponsive after writing default. Check wiring."));
    }
  }
}

void parseConfig(uint8_t config) {
  uint8_t pga_bits = config & CS1237_PGA_MASK;
  switch(pga_bits) {
    case CS1237_PGA_1:   pga_gain = 1.0; break;
    case CS1237_PGA_2:   pga_gain = 2.0; break;
    case CS1237_PGA_64:  pga_gain = 64.0; break;
    case CS1237_PGA_128: pga_gain = 128.0; break;
  }
  
  uint8_t speed_bits = config & CS1237_SPEED_MASK;
  switch(speed_bits) {
    case CS1237_SPEED_10HZ:   sample_rate_code = 0; break;
    case CS1237_SPEED_40HZ:   sample_rate_code = 1; break;
    case CS1237_SPEED_640HZ:  sample_rate_code = 2; break;
    case CS1237_SPEED_1280HZ: sample_rate_code = 3; break;
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
  
  waitForChipReady();

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

  return value;
}