/*                        -- 渡河蚂蚁电子工作室 --                        */
/*
*   说    明: STC8A8KS4A12 DEMO程序
*   开发平台: STC8A8KS4A12  
*   淘宝网店: https://shop136063510.taobao.com
*
//  文件名：main.c                                                              
//  说明：供客户测试模块通信使用程序                                                                  
//  编写人员：Duhemayi                                                                   
//  编写日期：2018-09-16                                                               
//  程序维护：
//  维护记录：
//	版    本: V1.0
//                                                          
// 免责声明：该程序仅用于学习与交流 
// (c) Duhemayi Corporation. All rights reserved.     
******************************************************************************/
#include "config.h"

unsigned char Flag_connect;
unsigned int MAX6675_Temp;

void MAIN_CLK_Config(void)
{
	#define CKSEL           (*(unsigned char volatile xdata *)0xfe00)
	#define CKDIV           (*(unsigned char volatile xdata *)0xfe01)
	#define IRC24MCR        (*(unsigned char volatile xdata *)0xfe02)
	#define XOSCCR          (*(unsigned char volatile xdata *)0xfe03)
	#define IRC32KCR        (*(unsigned char volatile xdata *)0xfe04)

	P_SW2 = 0x80;								//访问外设寄存器之前，先将P_SW2 BIT7置1
    CKSEL = 0x00;                               //选择主时钟源内部IRC ( 默认 )
    P_SW2 = 0x00;								//访问完外设寄存器之后，再将P_SW2 BIT7置0

    
//    P_SW2 = 0x80;
//    XOSCCR = 0xc0;                              //启动外部晶振
//    while (!(XOSCCR & 1));                      //等待时钟稳定
//    CKDIV = 0x00;                               //时钟不分频
//    CKSEL = 0x01;                               //选择外部晶振
//    P_SW2 = 0x00;
    

    /*
    P_SW2 = 0x80;
    IRC32KCR = 0x80;                            //启动内部32K IRC
    while (!(IRC32KCR & 1));                    //等待时钟稳定
    CKDIV = 0x00;                               //时钟不分频
    CKSEL = 0x03;                               //选择内部32K
    P_SW2 = 0x00;
    */
}

/******************************************************************************/
// 函数名称：main 
// 输入参数： 
// 输出参数： 
// 函数功能：打开外部中断0，按下按键改变LED的亮灭 

/******************************************************************************/
void main(void)
{	 
	unsigned long val;
//	uint8 a;

	{
		MAIN_CLK_Config();	//设置主时钟
		Uart1_Init();
		P1 = 0XFF;
		Con_CS1237();//配置CS1237芯片，这里验证了写时序
		Delay100ms();
		while(1)
		{
		  	val =  Read_CS1237();
//			a = Read_CON();		  //读取配置字
//			val = val*500/16777216;
//			val = val*250/16777216;
//			val = val*500/8388608;
			val = val*250/8388608;
			UART_Send_dat(val);
			UartSend(0x6d);
			UartSend(0x56);
			UartSend('\r');
			UartSend('\n');
//			UartSend(a);
			Delay100ms();
			Delay100ms();Delay100ms();Delay100ms();Delay100ms();
		}
	}	
}
 

