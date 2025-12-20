// 引脚定义
const int CS1237_SCLK = 5;
const int CS1237_DOUT_DRDY = 4;

// 全局变量
float pga_gain = 1;
int sample_rate = 0;
uint8_t cs1237_config = 0x00; // 默认配置: PGA=128, 10Hz, 通道A
float vref = 2.5; // 参考电压，通常是2.5V

void setup() {
  Serial.begin(115200);
  pinMode(CS1237_SCLK, OUTPUT);
  pinMode(CS1237_DOUT_DRDY, INPUT);
  digitalWrite(CS1237_SCLK, LOW);
  
  // 等待芯片稳定
  delay(200);
  
  Serial.println(F("CS1237 ADC - Enhanced Mode"));
  Serial.println(F("Commands: R=Read, A=Continuous, C=Configure, S=Status"));
  Serial.println(F("Send 's' to stop continuous reading"));
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
    default:
      if (command != '\n' && command != '\r') {
        showHelp();
      }
  }
}

// 改进的读取函数
void readAndDisplayData() {
  long adcValue = readCS1237ADC();
  
  if (adcValue == -1) {
    Serial.println(F("Data not ready or read error"));
    return;
  }
  
  // 严格的数据验证
  if (adcValue > 0xFFFFFF || adcValue < 0) {
    Serial.print(F("Invalid data range: "));
    Serial.println(adcValue);
    return;
  }
  
  // 转换为有符号24位
  long signedValue = adcValue;
  if (signedValue & 0x800000) {
    signedValue -= 0x1000000;
  }
  
  // 电压计算 - 修正公式
  // 满量程输入 = ±Vref / PGA
  float voltage = (signedValue / 8388608.0) * (vref / pga_gain);
  
  
  Serial.print(F(" | RAW ADC: "));
  Serial.print(signedValue);
  Serial.print(F(" | Voltage: "));
  Serial.print(voltage, 8);
  Serial.println(F(" V"));
  
  // 数据质量指示
  if (abs(signedValue) > 8000000) {
    Serial.println(F("Warning: Signal may be saturated"));
  }
}

void continuousRead() {
  Serial.println(F("Starting continuous reading... Send 's' to stop"));
  unsigned long lastReadTime = 0;
  const unsigned long readInterval = 100; // ms
  
  while (true) {
    // 检查停止命令
    if (Serial.available() > 0) {
      char stopChar = Serial.read();
      if (stopChar == 's' || stopChar == 'S') {
        Serial.println(F("Stopping continuous reading"));
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
  switch(pga) {
    case 0: pga_gain = 1.0; break;
    case 1: pga_gain = 2.0; break;
    case 2: pga_gain = 64.0; break;
    case 3: pga_gain = 128.0; break;
  }
  Serial.print(F("PGA gain set to: "));
  Serial.println(pga_gain);
}

void setSampleRate(int rate) {
  sample_rate = rate;
  Serial.print(F("Sample rate set to: "));
  switch(rate) {
    case 0: Serial.println(F("10 Hz")); break;
    case 1: Serial.println(F("40 Hz")); break;
    case 2: Serial.println(F("640 Hz")); break;
    case 3: Serial.println(F("1280 Hz")); break;
  }
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
  Serial.println(F("====================================="));
}

void showHelp() {
  Serial.println(F("\nAvailable commands:"));
  Serial.println(F("R - Single read"));
  Serial.println(F("A - Continuous read (send 's' to stop)"));
  Serial.println(F("C - Configuration mode"));
  Serial.println(F("S - Show current configuration"));
}

// 改进的ADC读取函数
long readCS1237ADC() {
  // 检查数据就绪信号
  if (digitalRead(CS1237_DOUT_DRDY) == HIGH) {
    return -1;
  }
  
  long value = 0;
  
  // 更精确的时序控制
  for (int i = 0; i < 24; i++) {
    digitalWrite(CS1237_SCLK, HIGH);
    delayMicroseconds(3); // 稍微增加延迟
    digitalWrite(CS1237_SCLK, LOW);
    delayMicroseconds(1); // 读取前短暂延迟
    
    int bitValue = digitalRead(CS1237_DOUT_DRDY);
    value = (value << 1) | bitValue;
    
    delayMicroseconds(1); // 位间延迟
  }
  
  // 验证读取的数据
  if (value == 0 || value == 0xFFFFFF) {
    return -1; // 可能的数据错误
  }
  
  return value;
}