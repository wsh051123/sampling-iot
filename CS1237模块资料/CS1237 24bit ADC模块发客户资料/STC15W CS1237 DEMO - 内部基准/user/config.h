/*                        -- 渡河蚂蚁电子工作室 --                        */
/*
*   说    明: STC8A8K64S4A12综合开发板配套程序
*   开发平台: STC8A8K64S4A12综合开发板     
*   淘宝网店: 
*
//  文件名：config.h                                                              
//  说明：供客户测试单片机的各项基本功能                                                                 
//  编写人员：Duhemayi                                                                   
//  编写日期：2019-7-8                                                              
//  程序维护：
//  维护记录：
//	版    本: V1.0
//                                                          
// 免责声明：该程序仅用于学习与交流 
// (c) Duhemayi Corporation. All rights reserved.     
******************************************************************************/
#ifndef _CONFIG_H
#define _CONFIG_H

/* 全局运行参数定义 */
#define MAIN_Fosc		11059200L	//定义主时钟
#define FOSC   11059200L  //系统主时钟频率，即振荡器频率÷12
#define	BRT	   (256 - MAIN_Fosc / 115200 / 32)


/* 通用头文件 */
#include <STC15Fxxxx.h>
#include <intrins.h>

#include "delay.h"
#include "uart.h"
#include "cs1237.h"

/* 数据类型定义 */
typedef  signed    char    int8;    // 8位有符号整型数
typedef  signed    int     int16;   //16位有符号整型数
typedef  signed    long    int32;   //32位有符号整型数
typedef  unsigned  char    uint8;   // 8位无符号整型数
typedef  unsigned  int     uint16;  //16位无符号整型数
typedef  unsigned  long    uint32;  //32位无符号整型数



/* IO引脚分配定义 */

sbit LED1 = P2^6;

#endif