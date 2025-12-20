#include "sys.h"
#include "delay.h"
#include "usart.h"
#include "led.h"
#include "tm1637.h"
#include "oled.h"
#include "bmp.h"
#include "cs1237.h"
 
/************************************************
 ALIENTEK战舰STM32开发板实验1
 跑马灯实验 
 技术支持：www.openedv.com
 淘宝店铺：http://eboard.taobao.com 
 关注微信公众平台微信号："正点原子"，免费获取STM32资料。
 广州市星翼电子科技有限公司  
 作者：正点原子 @ALIENTEK
************************************************/
 int main(void)
 {	
	 unsigned int tempA;
	 float dianya;
	 
	delay_init();	    //延时函数初始化	  
	LED_Init();		  	//初始化与LED连接的硬件接口
	 uart_init(115200);
	 
	OLED_Init();
	OLED_ColorTurn(0);//0正常显示，1 反色显示
	OLED_DisplayTurn(0);//0正常显示 1 屏幕翻转显示
	CS1237_GPIO_Init();
	delay_ms(100);
	Con_CS1237(RefOut_ON | SpeedSelct_1280HZ | PGA_1 | CH_A);//配置CS1237芯片
	while(1)
	{
		LED0=0;
		delay_ms(300);	 //延时300ms
		LED0=1;
		delay_ms(300);	//延时300ms
		
		OLED_ShowChinese(0,0,0,16,1);//渡
		OLED_ShowChinese(16,0,1,16,1);//河
		OLED_ShowChinese(32,0,2,16,1);//蚂
		OLED_ShowChinese(48,0,3,16,1);//蚁
		OLED_ShowChinese(64,0,4,16,1);//电
		OLED_ShowChinese(80,0,5,16,1);//子
		OLED_ShowChinese(10,18,6,16,1);//工
		OLED_ShowChinese(26,18,7,16,1);//作0000
		OLED_ShowChinese(42,18,8,16,1);//室
		OLED_ShowString(60,18,"CS1237",16,1);
		OLED_Refresh();
		
//		CS1237ReadInterlTemp();  //读取内部温度
		
		tempA =  Read_CS1237();
		dianya = tempA*1.25/8388608;
		if(PoolFlag == 1)
			printf("电压 dianya=-%10f v\r\n",dianya);//unsigned long 0～4294967295
		else
			printf("电压 dianya=+%10f v\r\n",dianya);//unsigned long 0～4294967295
		TM1637_SHOW(dianya*1000000);
		OLED_ShowDianya(dianya*1000000);
		
//		delay_ms(100);	
	}
 }


 /**
 *****************下面注视的代码是通过调用库函数来实现IO控制的方法*****************************************
int main(void)
{ 
 
	delay_init();		  //初始化延时函数
	LED_Init();		        //初始化LED端口
	while(1)
	{
			GPIO_ResetBits(GPIOB,GPIO_Pin_5);  //LED0对应引脚GPIOB.5拉低，亮  等同LED0=0;
			GPIO_SetBits(GPIOE,GPIO_Pin_5);   //LED1对应引脚GPIOE.5拉高，灭 等同LED1=1;
			delay_ms(300);  		   //延时300ms
			GPIO_SetBits(GPIOB,GPIO_Pin_5);	   //LED0对应引脚GPIOB.5拉高，灭  等同LED0=1;
			GPIO_ResetBits(GPIOE,GPIO_Pin_5); //LED1对应引脚GPIOE.5拉低，亮 等同LED1=0;
			delay_ms(300);                     //延时300ms
	}
} 
 
 ****************************************************************************************************
 ***/
 

	
/**
*******************下面注释掉的代码是通过 直接操作寄存器 方式实现IO口控制**************************************
int main(void)
{ 
 
	delay_init();		  //初始化延时函数
	LED_Init();		        //初始化LED端口
	while(1)
	{
     GPIOB->BRR=GPIO_Pin_5;//LED0亮
	   GPIOE->BSRR=GPIO_Pin_5;//LED1灭
		 delay_ms(300);
     GPIOB->BSRR=GPIO_Pin_5;//LED0灭
	   GPIOE->BRR=GPIO_Pin_5;//LED1亮
		 delay_ms(300);

	 }
 }
**************************************************************************************************
**/

