# ecg_live_view.py
# pip install matplotlib
import time
import glob
import os
from collections import deque
import matplotlib.pyplot as plt

# ==== CONFIG ====
ECG_DIR = r"./ecg_data"          # pasta onde o FastAPI salva os CSVs
SESSION_PREFIX = ""              # ex.: "S1a2b3c_9f8e7d"; deixe "" para pegar o mais recente de qualquer sessão
FS_HZ = 250                      # taxa de amostragem (só para escala; o CSV tem t_ms)
WINDOW_SEC = 10                  # janela de exibição no gráfico (segundos)
MA_WINDOW = 5                    # média móvel para suavizar (amostras). 0 para desativar
REFRESH_HZ = 20                  # taxa de atualização do gráfico

# ==== Estado ====
buf_t = deque(maxlen=FS_HZ*WINDOW_SEC*2)   # guardo *um pouco* mais que a janela para suavizar melhor
buf_v = deque(maxlen=FS_HZ*WINDOW_SEC*2)
buf_lo = deque(maxlen=FS_HZ*WINDOW_SEC*2)
current_file = None
fh = None
file_inode = None  # para detectar rotação
t0 = None

def find_latest_file():
    pattern = "*.csv" if not SESSION_PREFIX else f"{SESSION_PREFIX}__*.csv"
    files = glob.glob(os.path.join(ECG_DIR, pattern))
    if not files: 
        return None
    # pega o de modificação mais recente
    files.sort(key=lambda p: os.path.getmtime(p))
    return files[-1]

def open_tail(path):
    f = open(path, "rb", buffering=0)
    # posiciona no final existente (vamos apenas ler o que chega depois)
    f.seek(0, os.SEEK_END)
    return f

def parse_line(ln: bytes):
    # ignora comentários e linhas em branco
    if not ln or ln.startswith(b"#"): 
        return None
    try:
        s = ln.decode("utf-8").strip()
        if not s: 
            return None
        # esperado: t_ms,adc,lead_off
        parts = s.split(",")
        if len(parts) < 3: 
            return None
        t_ms = int(parts[0])
        adc = int(parts[1])
        lead_off = int(parts[2])
        return t_ms, adc, lead_off
    except Exception:
        return None

def moving_average(x, w):
    if w <= 1 or len(x) < w:
        return list(x)
    # média móvel simples incremental
    out = [0]*len(x)
    csum = 0
    for i, val in enumerate(x):
        csum += val
        if i >= w:
            csum -= x[i-w]
        if i >= w-1:
            out[i] = csum / w
        else:
            out[i] = val
    return out

def maybe_switch_file():
    global current_file, fh, file_inode, t0
    latest = find_latest_file()
    if latest is None:
        return
    if current_file is None:
        # primeira vez
        current_file = latest
        fh = open_tail(current_file)
        file_inode = os.stat(current_file).st_ino
        print(f"[live] seguindo arquivo: {current_file}")
        t0 = None
        return
    # houve rotação? (arquivo novo mais recente)
    if latest != current_file:
        try:
            # checa inode pra evitar falso positivo em Windows (não é perfeito)
            new_inode = os.stat(latest).st_ino
        except Exception:
            new_inode = None
        if new_inode != file_inode:
            try:
                fh.close()
            except Exception:
                pass
            current_file = latest
            fh = open_tail(current_file)
            file_inode = new_inode
            print(f"[live] trocando para novo arquivo: {current_file}")
            t0 = None

def read_new_lines():
    """Lê quaisquer linhas novas do arquivo seguido e abastece os buffers."""
    global t0
    if fh is None:
        return
    while True:
        pos = fh.tell()
        ln = fh.readline()
        if not ln:
            fh.seek(pos)  # nada novo; volta pro mesmo lugar
            break
        rec = parse_line(ln)
        if rec is None:
            continue
        t_ms, adc, lo = rec
        if t0 is None:
            t0 = t_ms
        t_rel = (t_ms - t0) / 1000.0
        buf_t.append(t_rel)
        buf_v.append(adc)
        buf_lo.append(lo)

def main():
    plt.ion()
    fig, ax = plt.subplots()
    line_raw, = ax.plot([], [], label="ECG (raw)")
    line_flt, = ax.plot([], [], label=f"ECG (MA{MA_WINDOW})" if MA_WINDOW > 1 else "ECG", linewidth=2)
    # Faixa para lead-off (vermelho sem cor específica)
    lead_off_marker, = ax.plot([], [], linestyle="none", marker="o", label="Lead-off", markersize=4)

    ax.set_xlabel("tempo (s)")
    ax.set_ylabel("ADC (contagens)")
    ax.set_title("ECG ao vivo (CSV em anexo contínuo)")
    ax.legend(loc="upper right")
    fig.tight_layout()

    last_draw = 0
    interval = 1.0 / max(REFRESH_HZ, 1)

    print("[live] aguardando arquivo aparecer e receber dados...")
    while True:
        maybe_switch_file()
        read_new_lines()

        now = time.time()
        if now - last_draw >= interval:
            last_draw = now
            if len(buf_t) > 2:
                # janela de exibição
                tmax = buf_t[-1]
                tmin = max(0.0, tmax - WINDOW_SEC)
                # recorta janela
                # (como usamos deques, filtramos por índice linear)
                # encontra primeiro índice >= tmin
                i0 = 0
                for i in range(len(buf_t)-1, -1, -1):
                    if buf_t[i] < tmin:
                        i0 = i + 1
                        break
                ts = list(buf_t)[i0:]
                vs = list(buf_v)[i0:]
                los = list(buf_lo)[i0:]

                # filtro (média móvel)
                vflt = moving_average(vs, MA_WINDOW) if MA_WINDOW > 1 else vs

                line_raw.set_data(ts, vs)
                line_flt.set_data(ts, vflt)

                # marca lead-off (pontos onde lo==1)
                x_lo = [t for t, lo in zip(ts, los) if lo == 1]
                y_lo = [v for v, lo in zip(vs, los) if lo == 1]
                lead_off_marker.set_data(x_lo, y_lo)

                # Ajusta eixos
                if ts:
                    ax.set_xlim(max(0, ts[0]), ts[-1] if ts[-1] > WINDOW_SEC else WINDOW_SEC)
                    # escala Y automática com margem
                    ymin = min(vs) if vs else 0
                    ymax = max(vs) if vs else 4095
                    if ymin == ymax:
                        ymin -= 10; ymax += 10
                    margin = (ymax - ymin) * 0.1
                    ax.set_ylim(ymin - margin, ymax + margin)

                plt.pause(0.001)
            else:
                # ainda sem dados
                plt.pause(0.05)

        time.sleep(0.01)  # cede CPU

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[live] encerrado pelo usuário.")
