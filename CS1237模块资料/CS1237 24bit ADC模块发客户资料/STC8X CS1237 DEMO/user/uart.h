#ifndef _UART_H
#define _UART_H

#include "config.h"

void Uart1_Init();
void UartSend(char dat);
void UartSendStr(char *p);
void UART_Send_dat(unsigned long dat);
void Uart_send_hex_to_txt(unsigned char dat);

#endif