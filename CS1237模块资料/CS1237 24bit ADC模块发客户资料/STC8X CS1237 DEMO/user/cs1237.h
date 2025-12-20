#ifndef __CS1237_H
#define __CS1237_H

#include "config.h"


//配置CS1237芯片
void Con_CS1237(void);
//读取芯片的配置数据
unsigned char Read_CON(void);
//读取ADC数据
unsigned long Read_CS1237(void);


#endif