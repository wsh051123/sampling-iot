#ifndef __CS1237_H
#define __CS1237_H

#define RefOut_OFF         0X40//关闭 REF 输出。
#define RefOut_ON          0X00//REF 正常输出。

#define SpeedSelct_10HZ    0x00//ADC 输出速率为 10Hz
#define SpeedSelct_40HZ    0x10//ADC 输出速率为 40Hz
#define SpeedSelct_640HZ   0x20//ADC 输出速率为 6400Hz
#define SpeedSelct_1280HZ  0x30//ADC 输出速率为 12800Hz

#define PGA_1              0X00//放大位数选择1
#define PGA_2              0X04//放大位数选择2
#define PGA_64             0X08//放大位数选择64
#define PGA_128            0X0C//放大位数选择128

#define CH_A               0X00//输入通道选择A
#define CH_Temp            0X02//输入通道选择内部温度测试
#define CH_Int             0X00//输入通道选择内部短路

extern unsigned char PoolFlag;

void CS1237_GPIO_Init(void);
//配置CS1237芯片
void Con_CS1237(unsigned char ch);
//读取芯片的配置数据
unsigned char Read_CON(void);
//读取ADC数据
unsigned long Read_CS1237(void);

void CS1237ReadInterlTemp(void);

#endif
