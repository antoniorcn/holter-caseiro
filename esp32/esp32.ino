#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

// ===== WIFI/HTTP =====
const char* WIFI_SSID = "<<WIFI SSID>>";
const char* WIFI_PASS = "<<WIFI SENHA>>";
// Ex.: "http://192.168.0.10:8000/ingest"
String SERVER_URL = "http://192.168.68.106:8000/ingest";

// ===== ECG Pinos =====
#define ECG_PIN       34   // AD8232 OUTPUT (ADC1)
#define LO_PLUS_PIN   32   // AD8232 LO+
#define LO_MINUS_PIN  33   // AD8232 LO-

// ===== Aquisição =====
const uint32_t FS_HZ = 250;                 // 250 Hz
const uint32_t SAMPLE_PERIOD_US = 1000000UL / FS_HZ;
volatile bool sampleFlag = false;
hw_timer_t* timer = nullptr;

// ===== Média por amostra =====
uint8_t AVERAGE_N = 4;                      // leituras por amostra

// ===== Blocos / Buffer =====
const uint32_t BLOCK_MS = 10000;            // duração do bloco: 10 s
String chunkBuffer;                          // acumula linhas t_ms,adc,lead_off
uint32_t blockStartMs = 0;
uint32_t chunkIndex = 0;

// ===== Fila de envio pendente (em memória) =====
const size_t MAX_PENDING = 12;               // ~2 min com blocos de 10 s
struct Pending {
  String payload;
  uint32_t index;
};
Pending pendingQueue[ MAX_PENDING ];
size_t qHead = 0, qTail = 0, qCount = 0;
uint32_t droppedBlocks = 0;

// ===== Sessão simples =====
String sessionId;

// ===== Timer ISR =====
void IRAM_ATTR onTimer() { sampleFlag = true; }

// ===== Util =====
bool wifiEnsure() {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - t0) < 15000) {
    delay(300);
  }
  return WiFi.status() == WL_CONNECTED;
}

void queuePush(const String& payload, uint32_t idx) {
  if (qCount == MAX_PENDING) {
    // fila cheia -> descarta o mais antigo
    qHead = (qHead + 1) % MAX_PENDING;
    qCount--;
    droppedBlocks++;
  }
  pendingQueue[qTail] = Pending{ payload, idx };
  qTail = (qTail + 1) % MAX_PENDING;
  qCount++;
}

bool queuePop(Pending &out) {
  if (qCount == 0) return false;
  out = pendingQueue[qHead];
  qHead = (qHead + 1) % MAX_PENDING;
  qCount--;
  return true;
}

// Envia um bloco por HTTP (text/csv no corpo)
bool sendChunkHTTP(const String& payload, uint32_t idx) {
  if (!wifiEnsure()) return false;

  HTTPClient http;
  // Metadados na querystring
  String url = SERVER_URL
    + "?session=" + sessionId
    + "&fs=" + String(FS_HZ)
    + "&avg=" + String(AVERAGE_N)
    + "&block_ms=" + String(BLOCK_MS)
    + "&chunk=" + String(idx);

  http.begin(url);
  http.addHeader("Content-Type", "text/csv");

  // envio como string (poderia ser stream)
  int code = http.POST((uint8_t*)payload.c_str(), payload.length());

  http.end();
  return (code >= 200 && code < 300);
}

// Finaliza o bloco atual: fecha buffer e tenta enviar; em caso de falha, enfileira
void finalizeBlock() {
  // garante newline final
  if (chunkBuffer.length() && chunkBuffer[chunkBuffer.length()-1] != '\n') {
    chunkBuffer += "\n";
  }

  String payload = chunkBuffer; // cópia
  uint32_t idx = chunkIndex++;

  // tenta enviar
  bool ok = sendChunkHTTP(payload, idx);
  if (!ok) {
    queuePush(payload, idx);
  }

  // prepara próximo bloco
  chunkBuffer = "";
  blockStartMs = millis();
}

// tenta despachar pendentes (1 por chamada para não travar)
void trySendPendingOnce() {
  if (qCount == 0) return;
  Pending p;
  if (queuePop(p)) {
    bool ok = sendChunkHTTP(p.payload, p.index);
    if (!ok) {
      // re-enfileira no final
      queuePush(p.payload, p.index);
      // evita loop apertado
      delay(50);
    }
  }
}

void setup() {
  Serial.begin(115200);

  // ADC
  analogReadResolution(12);
  analogSetPinAttenuation(ECG_PIN, ADC_11db);
  pinMode(LO_PLUS_PIN, INPUT);
  pinMode(LO_MINUS_PIN, INPUT);

  // Sessão (sem RTC: usa millis inicial e parte do MAC)
  uint64_t mac = ESP.getEfuseMac();
  sessionId = "S" + String((uint32_t)(millis() & 0xFFFFFF), 16) + "_" + String((uint32_t)(mac & 0xFFFFFF), 16);

  // Bloco inicial
  blockStartMs = millis();
  chunkBuffer.reserve(64 * 1024); // tenta reservar ~64 KB (ajuste se necessário)

  // Cabeçalho por bloco (opcional — backend não exige, mas ajuda depuração)
  chunkBuffer += "#device=ESP32+AD8232\n";
  chunkBuffer += "#cols=t_ms,adc,lead_off\n";

  // Conecta WiFi já no boot (opcional)
  wifiEnsure();

  // Timer 250 Hz
  timer = timerBegin(0, 80, true);                 // 80 MHz / 80 = 1 MHz
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, SAMPLE_PERIOD_US, true);  // 4000 us
  timerAlarmEnable(timer);
}

void loop() {
  // Amostragem
  if (sampleFlag) {
    sampleFlag = false;

    uint32_t acc = 0;
    for (uint8_t i = 0; i < AVERAGE_N; ++i) {
      acc += analogRead(ECG_PIN);
    }
    int rawAvg = (int)(acc / AVERAGE_N);
    bool leadOff = digitalRead(LO_PLUS_PIN) || digitalRead(LO_MINUS_PIN);
    uint32_t t = millis();

    // linha CSV
    // Atenção à memória: String é prática, mas se faltar RAM, trocar por um buffer char[]
    chunkBuffer.reserve(chunkBuffer.length() + 24); // heurística
    chunkBuffer += String(t);      chunkBuffer += ",";
    chunkBuffer += String(rawAvg); chunkBuffer += ",";
    chunkBuffer += (leadOff ? "1" : "0"); chunkBuffer += "\n";
  }

  // Rolar bloco por tempo
  uint32_t now = millis();
  if ((now - blockStartMs) >= BLOCK_MS) {
    finalizeBlock();
    // coloca cabeçalho opcional no próximo bloco
    chunkBuffer += "#device=ESP32+AD8232\n";
    chunkBuffer += "#cols=t_ms,adc,lead_off\n";
  }

  // Tenta despachar um pendente por iteração
  trySendPendingOnce();

  // Log eventual de queda de blocos
  static uint32_t lastReport = 0;
  if (now - lastReport > 5000) {
    lastReport = now;
    if (droppedBlocks > 0) {
      Serial.printf("[WARN] Blocos descartados por falta de memória: %lu\n", (unsigned long)droppedBlocks);
      droppedBlocks = 0;
    }
  }
}
