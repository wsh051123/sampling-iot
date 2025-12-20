/* MQTT (over TCP) Example

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "esp_wifi.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "esp_event.h"
#include "esp_netif.h"
// #include "protocol_examples_common.h"

#include "driver/uart.h"
#include "driver/gpio.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"

#include "lwip/sockets.h"
#include "lwip/dns.h"
#include "lwip/netdb.h"

#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"

static const char *TAG = "mqtt_example";

#define UART_PORT_NUM      UART_NUM_2
#define UART_BAUD_RATE     9600
#define TEST_TXD           4   // Arduino RX 接到了 ESP32 的 4
#define TEST_RXD           5   // Arduino TX 接到了 ESP32 的 5
#define RX_BUF_SIZE        1024

// 全局控制变量
static volatile bool g_collection_enable = true; // 默认开启采集
static volatile bool g_is_configuring = false;   // 是否正在配置中 (新增保护)

esp_mqtt_client_handle_t mqtt_client = NULL;

/* FreeRTOS event group to signal when we are connected*/
static EventGroupHandle_t s_wifi_event_group;

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

#define ESP_WIFI_SSID      "www"
#define ESP_WIFI_PASS      "wsh051123"
#define ESP_MAXIMUM_RETRY  5

static int s_retry_num = 0;

static void event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_num < ESP_MAXIMUM_RETRY) {
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGI(TAG, "retry to connect to the AP");
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
        ESP_LOGI(TAG,"connect to the AP fail");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

void wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &event_handler,
                                                        NULL,
                                                        &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                        IP_EVENT_STA_GOT_IP,
                                                        &event_handler,
                                                        NULL,
                                                        &instance_got_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = ESP_WIFI_SSID,
            .password = ESP_WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA) );
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config) );
    ESP_ERROR_CHECK(esp_wifi_start() );

    ESP_LOGI(TAG, "wifi_init_sta finished.");

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
            pdFALSE,
            pdFALSE,
            portMAX_DELAY);

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected to ap SSID:%s password:%s",
                 ESP_WIFI_SSID, ESP_WIFI_PASS);
    } else if (bits & WIFI_FAIL_BIT) {
        ESP_LOGI(TAG, "Failed to connect to SSID:%s, password:%s",
                 ESP_WIFI_SSID, ESP_WIFI_PASS);
    } else {
        ESP_LOGE(TAG, "UNEXPECTED EVENT");
    }
}

static const char test_data[] = "{"
    "\"id\": \"123\","
    "\"version\": \"1.0\","
    "\"params\": {"
        "\"test\": {"
            "\"value\": 55"
        "}"
    "}"
"}";

static void log_error_if_nonzero(const char *message, int error_code)
{
    if (error_code != 0) {
        ESP_LOGE(TAG, "Last error %s: 0x%x", message, error_code);
    }
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    ESP_LOGD(TAG, "Event dispatched from event loop base=%s, event_id=%" PRIi32 "", base, event_id);
    esp_mqtt_event_handle_t event = event_data;
    esp_mqtt_client_handle_t client = event->client;
    int msg_id;
    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT_EVENT_CONNECTED");
        
        msg_id = esp_mqtt_client_subscribe(client, "$sys/6R9kiumZF1/ESP32/thing/property/post/reply", 0);
        ESP_LOGI(TAG, "sent subscribe successful, msg_id=%d", msg_id);

        msg_id = esp_mqtt_client_subscribe(client, "$sys/6R9kiumZF1/ESP32/thing/property/set", 0);
        ESP_LOGI(TAG, "sent subscribe successful, msg_id=%d", msg_id);        

        msg_id = esp_mqtt_client_publish(client, "$sys/6R9kiumZF1/ESP32/thing/property/post", test_data, 0, 1, 0);
        ESP_LOGI(TAG, "sent publish successful, msg_id=%d", msg_id);
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGI(TAG, "MQTT_EVENT_DISCONNECTED");
        break;
    case MQTT_EVENT_SUBSCRIBED:
        ESP_LOGI(TAG, "MQTT_EVENT_SUBSCRIBED, msg_id=%d", event->msg_id);
        break;
    case MQTT_EVENT_UNSUBSCRIBED:
        ESP_LOGI(TAG, "MQTT_EVENT_UNSUBSCRIBED, msg_id=%d", event->msg_id);
        break;
    case MQTT_EVENT_PUBLISHED:
        ESP_LOGI(TAG, "MQTT_EVENT_PUBLISHED, msg_id=%d", event->msg_id);
        break;
    case MQTT_EVENT_DATA:
        if (event->topic_len > 0 && strstr(event->topic, "post/reply") != NULL) {
            ESP_LOGD(TAG, "Received Data ACK");
        } else {
            ESP_LOGI(TAG, "MQTT_EVENT_DATA");
            printf("TOPIC=%.*s\r\n", event->topic_len, event->topic);
            printf("DATA=%.*s\r\n", event->data_len, event->data);
        }

        if (event->topic_len == strlen("$sys/6R9kiumZF1/ESP32/thing/property/set") &&
            memcmp(event->topic, "$sys/6R9kiumZF1/ESP32/thing/property/set", event->topic_len) == 0) {
            
            ESP_LOGI(TAG, "Received Property Set Payload: %.*s", event->data_len, event->data);

            cJSON *root = cJSON_Parse(event->data);
            if (root) {
                cJSON *params = cJSON_GetObjectItem(root, "params");
                if (params) {
                    // --- 控制采集启停 ---
                    cJSON *enable_item = cJSON_GetObjectItem(params, "enable");
                    if (enable_item) {
                        if (cJSON_IsTrue(enable_item) || (cJSON_IsNumber(enable_item) && enable_item->valueint == 1)) {
                            g_collection_enable = true;
                            uart_write_bytes(UART_PORT_NUM, "A", 1); 
                            ESP_LOGI(TAG, "Command: Collection STARTED (Sent 'A')");
                        } else {
                            g_collection_enable = false;
                            uart_write_bytes(UART_PORT_NUM, "S", 1); 
                            ESP_LOGI(TAG, "Command: Collection STOPPED (Sent 'S')");
                        }
                    }

                    // --- 设置 PGA (pga: 1, 2, 64, 128) ---
                    cJSON *pga_item = cJSON_GetObjectItem(params, "pga");
                    if (pga_item && cJSON_IsNumber(pga_item)) {
                        char val_char = '0';
                        int val = pga_item->valueint;
                        bool valid = true;
                        
                        if (val == 1) val_char = '0';
                        else if (val == 2) val_char = '1';
                        else if (val == 64) val_char = '2';
                        else if (val == 128) val_char = '3';
                        else valid = false;

                        if (valid) {
                            g_is_configuring = true;
                            // 1. Enter Menu
                            uart_write_bytes(UART_PORT_NUM, "C", 1);
                            vTaskDelay(100 / portTICK_PERIOD_MS); // Wait for menu

                            // 2. Select PGA Option
                            uart_write_bytes(UART_PORT_NUM, "1", 1);
                            vTaskDelay(100 / portTICK_PERIOD_MS); // Wait for submenu

                            // 3. Send Value
                            uart_write_bytes(UART_PORT_NUM, &val_char, 1);
                            g_is_configuring = false;
                            
                            ESP_LOGI(TAG, "Command: Set PGA %d (Sent Sequence: C -> 1 -> %c)", val, val_char);
                        }
                    }

                    // --- 设置模式/采样率 (mode: 0=10Hz, 1=40Hz, 2=640Hz, 3=1280Hz) ---
                    // Arduino 逻辑: 收到 'F' 进菜单 -> 等待输入 '0'-'3'
                    cJSON *mode_item = cJSON_GetObjectItem(params, "mode");
                    if (mode_item && cJSON_IsNumber(mode_item)) {
                        char val_char = '0';
                        int val = mode_item->valueint;
                        
                        // 假设 OneNet 下发 0,1,2,3 直接对应 Arduino 的 0,1,2,3
                        if (val >= 0 && val <= 3) {
                            val_char = '0' + val;
                            
                            g_is_configuring = true;
                            uart_write_bytes(UART_PORT_NUM, "F", 1);
                            vTaskDelay(100 / portTICK_PERIOD_MS);
                            uart_write_bytes(UART_PORT_NUM, &val_char, 1); 
                            g_is_configuring = false;

                            ESP_LOGI(TAG, "Command: Set Rate Code %d (Sent Sequence: F -> %c)", val, val_char);
                        }
                    }
                }

                // 回复 OneNet
                cJSON *id = cJSON_GetObjectItem(root, "id");
                if (cJSON_IsString(id) && (id->valuestring != NULL)) {
                    char reply_data[128];
                    snprintf(reply_data, sizeof(reply_data), "{\"id\":\"%s\",\"code\":200,\"msg\":\"success\"}", id->valuestring);
                    msg_id = esp_mqtt_client_publish(client, "$sys/6R9kiumZF1/ESP32/thing/property/set_reply", reply_data, 0, 1, 0);
                }
                cJSON_Delete(root);
            }
        }
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGI(TAG, "MQTT_EVENT_ERROR");
        break;
    default:
        break;
    }
}

static void mqtt_app_start(void)
{
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = "mqtt://mqtts.heclouds.com:1883",
        .credentials.username = "6R9kiumZF1",
        .credentials.client_id = "ESP32",
        .credentials.authentication.password = 
            "version=2018-10-31&res=products%2F6R9kiumZF1%2Fdevices%2FESP32&et=1923202207&method=md5&sign=S9SRMkTDgNQcH9lEVh%2Bnew%3D%3D",
    };
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    mqtt_client = client;
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
}

void init_uart(void) {
    const uart_config_t uart_config = {
        .baud_rate = UART_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT_NUM, RX_BUF_SIZE * 2, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PORT_NUM, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT_NUM, TEST_TXD, TEST_RXD, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    printf("UART2 initialized on TX=%d, RX=%d\n", TEST_TXD, TEST_RXD);
}

static void rx_task(void *arg)
{
    uint8_t byte_in;
    int state = 0;
    uint8_t frame_buffer[10];
    int data_idx = 0;
    
    printf("UART RX Task Started!\n");
    TickType_t last_data_time = xTaskGetTickCount();

    printf("Sending start command 'A' to Arduino...\n");
    uart_write_bytes(UART_PORT_NUM, "A", 1);

    while (1) {
        // 如果采集被禁用，暂停任务
        if (!g_collection_enable) {
            vTaskDelay(1000 / portTICK_PERIOD_MS);
            continue;
        }

        // 如果正在配置，暂停接收和超时重发，避免干扰配置序列
        if (g_is_configuring) {
            vTaskDelay(100 / portTICK_PERIOD_MS);
            last_data_time = xTaskGetTickCount(); // 刷新计时，防止配置完立刻超时
            continue;
        }

        // 超时重发 'A' (仅在非配置状态下)
        if ((xTaskGetTickCount() - last_data_time) > (2000 / portTICK_PERIOD_MS)) {
            printf("Timeout! No data from Arduino. Resending 'A'...\n");
            uart_write_bytes(UART_PORT_NUM, "A", 1);
            last_data_time = xTaskGetTickCount(); 
        }

        int len = uart_read_bytes(UART_PORT_NUM, &byte_in, 1, 100 / portTICK_PERIOD_MS);
        if (len > 0) {
            last_data_time = xTaskGetTickCount();
            
            switch(state) {
                case 0:
                    if (byte_in == 0xAA) {
                        frame_buffer[0] = byte_in;
                        state = 1;
                    }
                    break;
                case 1:
                    if (byte_in == 0x55) {
                        frame_buffer[1] = byte_in;
                        data_idx = 2;
                        state = 2;
                    } else {
                        state = 0;
                        if (byte_in == 0xAA) state = 1;
                    }
                    break;
                case 2:
                    frame_buffer[data_idx++] = byte_in;
                    if (data_idx == 10) {
                        if (frame_buffer[8] == 0x0D && frame_buffer[9] == 0x0A) {
                            float voltage;
                            memcpy(&voltage, &frame_buffer[2], 4);
                            uint16_t pga;
                            memcpy(&pga, &frame_buffer[6], 2);

                            ESP_LOGI(TAG, "UART Recv: %.4f V (PGA=%d)", voltage, pga);

                            if (mqtt_client) {
                                char payload[200];
                                snprintf(payload, sizeof(payload), 
                                    "{\"id\":\"%d\",\"version\":\"1.0\",\"params\":{\"voltage\":{\"value\":%.4f},\"pga\":{\"value\":%d}}}", 
                                    (int)xTaskGetTickCount(), voltage, pga);
                                
                                esp_mqtt_client_publish(mqtt_client, "$sys/6R9kiumZF1/ESP32/thing/property/post", payload, 0, 1, 0);
                            }
                        }
                        state = 0;
                    }
                    break;
            }
        }
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "[APP] Startup..");
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_sta();
    mqtt_app_start();
    
    init_uart();
    xTaskCreate(rx_task, "uart_rx_task", 1024*4, NULL, 5, NULL);
}
