# Holter Caseiro com ESP32 + AD8232

⚠️ **Aviso importante**: este projeto tem **fins exclusivamente didáticos/maker**.  
Não é um equipamento médico homologado, não deve ser usado para diagnóstico e pode apresentar falhas.  
Sempre utilize o ESP32 alimentado por **bateria** (nunca conectado via USB ao computador enquanto os eletrodos estão no corpo).  
Se você possui marcapasso, problemas cardíacos ou qualquer condição de saúde, **não use**.

---

## 📖 Visão geral

Este projeto demonstra como montar um **registrador ECG estilo Holter caseiro** utilizando:

- **ESP32 DevKit**  
- **Módulo AD8232** com 3 eletrodos descartáveis  
- **Backend em Python (FastAPI)** para receber dados via HTTP e salvar em CSV  
- **Scripts em Python** para visualização contínua:
  - `matplotlib` (visual simples, janela deslizante)
  - `pygame` (simulação de papel de ECG, com grade em mm e velocidade configurável)

---

## 🛠️ Hardware necessário

- Placa **ESP32 DevKit** (WROOM/WROVER)  
- Módulo **AD8232 ECG** com cabos (cores padrão: Vermelho, Amarelo, Verde)  
- Eletrodos descartáveis de ECG (gel)  
- Bateria LiPo 3,7 V (ou power bank USB → 5V → 3.3V da placa)  
- Cabos jumper

### Conexões principais

- AD8232 **OUTPUT** → ESP32 **GPIO34** (ADC1)  
- AD8232 **LO+** → ESP32 **GPIO32**  
- AD8232 **LO-** → ESP32 **GPIO33**  
- AD8232 **VCC** → 3.3V ESP32  
- AD8232 **GND** → GND ESP32  

### Colocação dos eletrodos

- **Vermelho (RA – Right Arm)** → abaixo da clavícula direita  
- **Amarelo (LA – Left Arm)** → abaixo da clavícula esquerda  
- **Verde (RL – Right Leg / GND)** → costela inferior direita  

---

## 📂 Estrutura do projeto

holter_caseiro/
│
├── esp32/ # código do firmware (Arduino/PlatformIO)
│ └── ecg_http_logger.ino
│
├── backend/ # servidor FastAPI
│ └── main.py
│
├── scripts/ # scripts Python para análise/visualização
│ ├── ecg_live_view.py # matplotlib, janela deslizante
│ └── ecg_paper_pygame.py # pygame, estilo papel ECG
│
└── README.md

---

## 🚀 Backend (FastAPI)

### Instalação
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
# ou source .venv/bin/activate no Linux/Mac
pip install fastapi uvicorn python-multipart
```

### Execução
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

![Screen Shot Backend](./assets/screen%20shot%20backend.png "Screen Shot Backend")

Os arquivos CSV serão salvos em ./ecg_data/

Documentação da API em http://localhost:8000/docs

### 🔌 Firmware ESP32
Funcionalidades
Amostragem a 250 Hz (configurável)

Média de N leituras por amostra para suavizar

Envio contínuo de blocos CSV via HTTP POST para o backend

Retentativa em caso de falha de rede

![Screen Shot ESP32](./assets/esp32%20board.jpeg "Screen Shot ESP32")

Upload
Instale o Arduino IDE ou PlatformIO.

Configure a placa como ESP32 Dev Module.

Ajuste as credenciais WiFi e URL do backend no código:
```C++
const char* WIFI_SSID = "SEU_SSID";
const char* WIFI_PASS = "SUA_SENHA";
String SERVER_URL = "http://SEU_PC:8000/ingest";
```

Compile e faça upload para o ESP32.

### 📊 Visualização em Python
1. Matplotlib (janela deslizante)
``` bash
pip install matplotlib
python scripts/ecg_live_view.py
```

![Screen Shot Viewer](./assets/screen%20shot%20viewer.png "Screen Shot Viewer")

Mostra gráfico contínuo de 10s, atualizando em tempo real.

2. Pygame (estilo papel ECG - ainda em desenvolvimento não está funcionando corretamente)
```bash
pip install pygame
python scripts/ecg_paper_pygame.py
```
Grade em mm (linhas finas a cada 1 mm, grossas a cada 5 mm)

Velocidade configurável (25 ou 50 mm/s)

Ganho configurável (10 mm/mV)

### ️ Ajustes importantes
FS_HZ: taxa de amostragem (default 250 Hz)

PAPER_SPEED_MM_S: 25 ou 50 mm/s (padrão clínico)

GAIN_MM_PER_MV: 10 mm/mV (padrão clínico)

BASELINE_WINDOW_S: filtro simples de linha de base

### 📌 Próximos passos (opcionais)
Adicionar detecção automática de picos R → cálculo de frequência cardíaca (bpm)

Compressão gzip dos blocos CSV para economizar banda

Autenticação por token no backend

Salvar também em banco de dados (ex.: SQLite ou InfluxDB)

### 🧑‍⚕️ Aviso final
Este projeto é apenas uma prova de conceito de engenharia.
Ele não deve ser usado como equipamento médico.
Se você deseja registrar ECG para saúde, procure um Holter clínico certificado.