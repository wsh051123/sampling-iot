#include "cs1237.h"
#include "sys.h"
#include "delay.h"
#include "usart.h"

//SCK   PA5
//SDI/O PA7

#define ADC_Bit  20 //ADC有效位数，带符号位 最高24位
#define SCK_1  GPIO_SetBits(GPIOA,GPIO_Pin_5)//SCLK = 1
#define SCK_0  GPIO_ResetBits(GPIOA,GPIO_Pin_5)//SCLK = 0
#define DAT_1  GPIO_SetBits(GPIOA,GPIO_Pin_7)//DOUT = 1
#define DAT_0  GPIO_ResetBits(GPIOA,GPIO_Pin_7)//DOUT = 0

#define	NOP_5()		delay_us(5);
#define	NOP30()		delay_us(30);
#define	NOP40()		delay_us(5);
#define One_CLK  SCK_1;NOP40();SCK_0;NOP40();
#define CS_CON  0X00   //芯片地配置 内部REF 输出40HZ PGA=128 通道A 0X1C   

unsigned char PoolFlag;

void CS1237_GPIO_Init(void)
{
	GPIO_InitTypeDef  GPIO_InitStructure;
 	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);	 //使能A端口时钟
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_5|GPIO_Pin_7;	 
 	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP; 		 //推挽输出
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;//速度50MHz
 	GPIO_Init(GPIOA, &GPIO_InitStructure);	  //初始化PA0,1
 	GPIO_SetBits(GPIOA,GPIO_Pin_5|GPIO_Pin_7);
}

void CS1237_SDA_SetInput(void)
{
	GPIO_InitTypeDef  GPIO_InitStructure;				     //PA.7 输出高

	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_7;				 //PA7
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;           //上拉输入
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;		 //IO口速度为50MHz
	GPIO_Init(GPIOA, &GPIO_InitStructure);					 //根据设定参数初始化GPIOA.7
}

void CS1237_SDA_SetOutput(void)
{
	GPIO_InitTypeDef  GPIO_InitStructure;				     //PA.7 输出高

	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_7;				 //PA7
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP; 		 //开漏
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;		 //IO口速度为50MHz
	GPIO_Init(GPIOA, &GPIO_InitStructure);					 //根据设定参数初始化GPIOA.7
}

//延时500US 25MHZ
void delay_500us(unsigned char a)
{	
	unsigned char i,j,b;
	for(b=0;b<a;b++)
	{
		i = 13;
		j = 37;
		do
		{
			while (--j);
		} while (--i);
	}
}

//配置CS1237芯片
void Con_CS1237(unsigned char ConfigDat)
{
	unsigned char i;
	unsigned char dat;
	unsigned char count_i=0;//溢出计时器
	
	dat = ConfigDat;
	SCK_0;//时钟拉低
	CS1237_SDA_SetInput();
	while(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7))//芯片准备好数据输出  时钟已经为0，数据也需要等CS1237全部拉低为0才算都准备好
	{
		delay_500us(10);
		count_i++;
		if(count_i > 150)
		{
			CS1237_SDA_SetOutput();
			SCK_1;
			DAT_1;
			return;//超时，则直接退出程序
		}
	}
	for(i=0;i<29;i++)// 1 - 29
	{
//		One_CLK;
		SCK_1;
		NOP40();
		SCK_0;
		NOP40();
	}
	CS1237_SDA_SetOutput();
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//30
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//31
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//32
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//33
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//34
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//35
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//36
//	DAT_0;
	One_CLK;//37     写入了0x65
	for(i=0;i<8;i++)// 38 - 45个脉冲了，写8位数据
	{
		
		if(dat&0x80)
			DAT_1;
		else
			DAT_0;
		dat <<= 1;
		SCK_1;
		NOP40();
		SCK_0;
		NOP40();
	}
	One_CLK;//46个脉冲拉高数据引脚
}

//读取芯片的配置数据
unsigned char Read_CON(void)
{
	unsigned char i;
	unsigned char dat=0;//读取到的数据
	unsigned char count_i=0;//溢出计时器
//	unsigned char k=0,j=0;//中间变量
	
	SCK_0;//时钟拉低
	CS1237_SDA_SetInput();
	while(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7))//芯片准备好数据输出  时钟已经为0，数据也需要等CS1237全部拉低为0才算都准备好
	{
		delay_500us(10);
		count_i++;
		if(count_i > 150)
		{
			CS1237_SDA_SetOutput();
			SCK_1;
			DAT_1;
			return 1;//超时，则直接退出程序
		}
	}
	CS1237_SDA_SetOutput();
	for(i=0;i<29;i++)// 1 - 29
	{
		One_CLK;
	}
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//30
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//31
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//32
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//33
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//34
	DAT_1;SCK_1;NOP30();SCK_0;NOP30();//35
	DAT_0;SCK_1;NOP30();SCK_0;NOP30();//36
	DAT_1;
	One_CLK;//37     写入了0x56
//	DAT_0;
	CS1237_SDA_SetInput();
	for(i=0;i<8;i++)// 38 - 45个脉冲了，读取数据
	{
		One_CLK;
		dat <<= 1;
		if(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7))
			dat++;
	}
	One_CLK;//46个脉冲拉高数据引脚
	CS1237_SDA_SetOutput();
	DAT_1;
	
	return dat;
}

//读取ADC数据，返回的是一个有符号数据
unsigned long Read_CS1237(void)
{
	unsigned char i;
	unsigned long dat=0;//读取到的数据
	unsigned char count_i=0;//溢出计时器
	DAT_1;//端口锁存1，51必备
	SCK_0;//时钟拉低
	CS1237_SDA_SetInput();
	while(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7) == 0);
	while(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7))//芯片准备好数据输出  时钟已经为0，数据也需要等CS1237拉低为0才算都准备好
	{
		delay_500us(10);
		count_i++;
		if(count_i > 300)
		{
			CS1237_SDA_SetOutput();
			SCK_1;
			DAT_1;
			return 0;//超时，则直接退出程序
		}
	}
	DAT_1;//端口锁存1，51必备
	dat=0;
	CS1237_SDA_SetInput();
	for(i=0;i<24;i++)//获取24位有效转换
	{
		SCK_1;
		NOP40();
		dat <<= 1;
		if(GPIO_ReadInputDataBit(GPIOA,GPIO_Pin_7))
			dat ++;
		SCK_0;
		NOP40();	
	}
//	for(i=0;i<3;i++)//一共输入27个脉冲
//	{
//		One_CLK;
//	}
	CS1237_SDA_SetOutput();
	DAT_1;
	
//	Uart_send_hex_to_txt(dat>>16);
//	Uart_send_hex_to_txt(dat>>8);
//	Uart_send_hex_to_txt(dat);
	printf("ad val=%10X \r\n",dat);//unsigned long 0～4294967295
	
	if((dat&0x800000) == 0x800000)	//最高位为1，表示输入为负值
	{
		dat = ~dat;
		dat =dat+1;
		dat =dat&0xffffff;
		PoolFlag = 1;
//		UART_Send_Byte(0x2D);				// - 号
	}
	else 
	{
		PoolFlag = 0;
//		UART_Send_Byte(0x2B);				//+  号
	}
		
	//先根据宏定义里面的有效位，丢弃一些数据
//	i = 24 - ADC_Bit;//i表示将要丢弃的位数
//	dat >>= i;//丢弃多余的位数
	
	return dat;
}
//
//----------------------------------------------------------------------------------
// 读取CS1237的内部温度
//----------------------------------------------------------------------------------
void CS1237ReadInterlTemp(void)
{
	uint8_t config;
	
	Con_CS1237(RefOut_OFF | SpeedSelct_1280HZ | PGA_1 | CH_Temp);//配置CS1237芯片
	delay_ms(500);
	
	
	while(1)
	{
		Read_CS1237();
		delay_ms(1);
	}
}
