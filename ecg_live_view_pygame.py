# ecg_paper_pygame.py
# pip install pygame
import os, glob, time
import pygame
from collections import deque

# ========= CONFIGURAÇÕES =========
ECG_DIR = r"./ecg_data"     # pasta onde o FastAPI salva os CSVs
SESSION_PREFIX = ""         # ex.: "Sabc123_" para seguir só uma sessão; "" = qualquer sessão
FS_HZ = 250                 # taxa de amostragem (do seu logger)
PAPER_SPEED_MM_S = 25       # 25 ou 50 mm/s (padrão clínico)
GAIN_MM_PER_MV = 10         # ganho: 10 mm/mV (padrão clínico)
PX_PER_MM = 4               # densidade da grade (4 px ≈ 1 mm fica bom em telas comuns)
ADC_BITS = 12               # resolução do ADC do ESP32
VREF = 3.3                  # tensão de referência aproximada do ADC
CENTER_V = VREF / 2.0       # ECG do AD8232 sai centrado em ~VCC/2
LEAD_OFF_VALUE = None       # None = mostra traçado mesmo com lead-off; ou 0 p/ "zerar" quando lead-off=1

# Filtro simples de baseline (média móvel sobre N amostras). 0 = desativado
BASELINE_WINDOW_S = 0.6     # ~0.5–1.0 s suaviza "wander"
# Janela visual (segundos) = largura da tela / (mm/s). Ajustada automaticamente por largura
SCREEN_W_PX = 1280          # largura da janela
SCREEN_H_PX = 480           # altura da janela
LINE_THIN = (180, 60, 60)   # cor das linhas finas (grade 1 mm)
LINE_BOLD = (160, 30, 30)   # cor das linhas 5 mm
BG_COLOR  = (255, 245, 245) # cor “papel” ECG
ECG_COLOR = (0, 0, 0)       # cor do traçado
LO_COLOR  = (200, 0, 0)     # cor quando lead-off
FPS_DRAW = 60               # taxa de desenho da tela (independente de amostragem)

# ========= DERIVADOS =========
SAMPLE_DT = 1.0 / FS_HZ
PX_PER_SEC = PAPER_SPEED_MM_S * PX_PER_MM
PX_PER_SAMPLE = PX_PER_SEC / FS_HZ
MV_PER_COUNT = VREF / (2**ADC_BITS) * (1000.0 / 1.0)  # mV por contagem (aprox)
# Conversão: (adc*VREF/4096 - CENTER_V) -> volts -> mV -> pixels (ganho 10 mm/mV)
MM_PER_MV = GAIN_MM_PER_MV
PX_PER_MV = MM_PER_MV * PX_PER_MM

BASELINE_WINDOW = int(BASELINE_WINDOW_S * FS_HZ) if BASELINE_WINDOW_S > 0 else 0

# ========= LEITURA “TAIL” DO CSV =========
def find_latest_file():
    pattern = "*.csv" if not SESSION_PREFIX else f"{SESSION_PREFIX}__*.csv"
    files = glob.glob(os.path.join(ECG_DIR, pattern))
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p))
    return files[-1]

def open_tail(path):
    f = open(path, "rb", buffering=0)
    f.seek(0, os.SEEK_END)  # começa do fim
    return f

def parse_line(ln: bytes):
    if not ln or ln.startswith(b"#"):
        return None
    try:
        s = ln.decode("utf-8").strip()
        if not s:
            return None
        t_ms, adc, lead_off = s.split(",")[:3]
        return int(t_ms), int(adc), int(lead_off)
    except Exception:
        return None

class CsvTail:
    def __init__(self, ecg_dir):
        self.ecg_dir = ecg_dir
        self.cur_path = None
        self.fh = None
        self.inode = None

    def _switch_if_new(self):
        latest = find_latest_file()
        if latest is None:
            return
        if self.cur_path is None:
            self.cur_path = latest
            self.fh = open_tail(latest)
            try:
                self.inode = os.stat(latest).st_ino
            except Exception:
                self.inode = None
            print(f"[tail] seguindo: {self.cur_path}")
            return
        if latest != self.cur_path:
            # trocou o arquivo (virou a hora)
            try:
                new_inode = os.stat(latest).st_ino
            except Exception:
                new_inode = None
            if new_inode != self.inode:
                try:
                    self.fh.close()
                except Exception:
                    pass
                self.cur_path = latest
                self.fh = open_tail(latest)
                self.inode = new_inode
                print(f"[tail] novo arquivo: {self.cur_path}")

    def read_new(self, max_lines=4096):
        self._switch_if_new()
        if not self.fh:
            return []
        out = []
        for _ in range(max_lines):
            pos = self.fh.tell()
            ln = self.fh.readline()
            if not ln:
                self.fh.seek(pos)
                break
            rec = parse_line(ln)
            if rec:
                out.append(rec)
        return out

# ========= RENDER “PAPEL” =========
def draw_grid(surface):
    surface.fill(BG_COLOR)
    w, h = surface.get_size()
    # linhas verticais cada 1 mm
    for x in range(0, w, PX_PER_MM):
        color = LINE_BOLD if (x // PX_PER_MM) % 5 == 0 else LINE_THIN
        pygame.draw.line(surface, color, (x, 0), (x, h), 1)
    # linhas horizontais cada 1 mm
    for y in range(0, h, PX_PER_MM):
        color = LINE_BOLD if (y // PX_PER_MM) % 5 == 0 else LINE_THIN
        pygame.draw.line(surface, color, (0, y), (w, y), 1)

# ========= CONVERSÃO E FILTRO =========
class BaselineRemover:
    def __init__(self, win):
        self.win = win
        self.buf = deque(maxlen=win) if win > 1 else None
        self.sum = 0.0

    def step(self, mv):
        if not self.buf:
            return mv
        if len(self.buf) == self.buf.maxlen:
            self.sum -= self.buf[0]
        self.buf.append(mv)
        self.sum += mv
        mean = self.sum / len(self.buf)
        return mv - mean

def adc_to_mv(adc):
    v = (adc / float(2**ADC_BITS)) * VREF
    mv = (v - CENTER_V) * 1000.0
    return mv

def mv_to_px(mv, center_y_px):
    return int(round(center_y_px - mv * PX_PER_MV))

# ========= MAIN LOOP =========
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W_PX, SCREEN_H_PX))
    pygame.display.set_caption("ECG - Pygame (papel)")
    clock_draw = pygame.time.Clock()
    clock_sample = pygame.time.Clock()

    draw_grid(screen)
    pygame.display.flip()

    # superfície para traço (sobre a grade)
    trace = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    trace.fill((0, 0, 0, 0))

    center_y = SCREEN_H_PX // 2

    # posição x do “caneta” que desenha; rola à esquerda apagando atrás
    pen_x = 0
    # largura de avanço por amostra (pode ser < 1px; acumulamos erro)
    advance = PX_PER_SAMPLE
    acc_x = 0.0

    tail = CsvTail(ECG_DIR)
    bl = BaselineRemover(BASELINE_WINDOW)

    running = True
    last_mv = 0.0
    last_y = center_y

    # Mostrador de escala (1 mV = 10 mm; 1 s = 25/50 mm)
    font = pygame.font.SysFont("Arial", 16)

    # buffer de amostras lidas mas ainda não desenhadas
    sample_queue = deque()

    # sincronização: queremos processar ~FS_HZ amostras por segundo
    t_next = time.perf_counter()

    while running:
        # --- eventos ---
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False

        # --- input/tail ---
        # busca novas linhas, adiciona à fila
        new_recs = tail.read_new(max_lines=4096)
        for _, adc, lo in new_recs:
            # lead-off opcionalmente "achata"
            if LEAD_OFF_VALUE is not None and lo == 1:
                adc_use = int((CENTER_V / VREF) * (2**ADC_BITS))  # centro
            else:
                adc_use = adc
            mv = adc_to_mv(adc_use)
            mv = bl.step(mv)
            sample_queue.append((mv, lo))

        # --- consumo de amostras no ritmo do tempo real ---
        now = time.perf_counter()
        while t_next <= now and sample_queue:
            mv, lo = sample_queue.popleft()
            # avanço horizontal acumulando frações
            acc_x += advance
            step_px = int(acc_x)
            acc_x -= step_px
            if step_px == 0:
                step_px = 1  # garante um traço mínimo

            # “apaga” uma faixa à frente para dar efeito de rolagem (redesenha grade no screen, mas o trace apaga só o que precisa)
            # ao invés de apagar no trace (transparente), a gente desenha por cima com BG translúcido para não deixar “fantasmas”
            erase_rect = pygame.Rect(pen_x, 0, step_px, SCREEN_H_PX)
            trace.fill((0, 0, 0, 0), erase_rect)

            # calcula y atual
            y = mv_to_px(mv, center_y)
            # cor conforme lead-off
            color = LO_COLOR if (lo == 1 and LEAD_OFF_VALUE is None) else ECG_COLOR

            # desenha do ponto anterior ao novo x
            new_x = pen_x + step_px
            pygame.draw.line(trace, color, (pen_x, last_y), (new_x, y), 2)

            pen_x = new_x

            # wrap quando chega no fim da tela (reinicia na esquerda)
            if pen_x >= SCREEN_W_PX:
                pen_x = 0

            last_y = y
            last_mv = mv

            t_next += SAMPLE_DT  # próxima amostra “no tempo”

        # --- composição e HUD ---
        # redesenha grade fixa (uma vez) e “cola” o traço por cima
        screen.fill(BG_COLOR)
        draw_grid(screen)
        screen.blit(trace, (0, 0))

        # desenho do cursor vertical da “caneta”
        pygame.draw.line(screen, (50, 50, 50), (pen_x, 0), (pen_x, SCREEN_H_PX), 1)

        # HUD de escala/velocidade
        txt = f"{PAPER_SPEED_MM_S} mm/s   {GAIN_MM_PER_MV} mm/mV   fs={FS_HZ} Hz"
        surf = font.render(txt, True, (40, 40, 40))
        screen.blit(surf, (10, 10))

        pygame.display.flip()
        clock_draw.tick(FPS_DRAW)

        # se buffer ficou vazio, dá um descanso pequeno (sem atrasar quando tem dados)
        if not sample_queue:
            time.sleep(0.002)

    pygame.quit()

if __name__ == "__main__":
    main()
