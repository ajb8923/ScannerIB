# Scanner IB — ruptura de la primera hora · GitHub Actions + Telegram
import os, json, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd, requests, yfinance as yf

SYMBOLS = {
    "DAX": {"label": "DAX 40",     "yf": "^GDAXI", "tz": "Europe/Berlin",    "open": "09:00", "close": "17:30"},
    "NDX": {"label": "Nasdaq 100", "yf": "^NDX",   "tz": "America/New_York", "open": "09:30", "close": "16:00"},
    "DJI": {"label": "Dow Jones",  "yf": "^DJI",   "tz": "America/New_York", "open": "09:30", "close": "16:00"},
    "SPX": {"label": "S&P 500",    "yf": "^GSPC",  "tz": "America/New_York", "open": "09:30", "close": "16:00"},
}
# Configuración de las alertas de Telegram (la web tiene sus propios interruptores)
CFG = {"ib_min": 60, "rr": 2.0, "atr_len": 14, "atr_mult": 2.5, "news": True,
       "A": True, "B": True, "C": True, "long": True, "short": True,
       "gap": 5}  # setup A: la vela siguiente debe abrir al menos estos puntos fuera del IB
TOKEN, CHAT = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
MAX_AGE = 15  # minutos: las señales más antiguas no se envían (llegarían tarde para entrar)

def hm(s):
    h, m = map(int, s.split(":")); return h * 60 + m

def load(s):
    df = yf.Ticker(s["yf"]).history(period="60d", interval="5m", auto_adjust=False)
    if df.empty: return [], []
    df = df[df.index + pd.Timedelta(minutes=5) <= pd.Timestamp.now(tz="UTC")]  # solo velas cerradas
    loc = df.index.tz_convert(s["tz"])
    mins = loc.hour * 60 + loc.minute
    mask = (mins >= hm(s["open"])) & (mins < hm(s["close"]))
    df, loc, mins = df[mask], loc[mask], mins[mask]
    lon = df.index.tz_convert("Europe/London").tz_localize(None)
    tl = (lon - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)  # hora de Londres
    dates = [d.strftime("%Y-%m-%d") for d in loc]
    days = sorted(set(dates)); di = {d: i for i, d in enumerate(days)}
    bars = [[int(t), round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2), int(m), di[d], int(v or 0)]
            for t, o, h, l, c, v, m, d in zip(tl, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"].fillna(0), mins, dates)]
    return bars, days

def atr(bars, n):
    out, trs = [], []
    for i, b in enumerate(bars):
        pc = bars[i - 1][4] if i else b[4]
        trs.append(max(b[2] - b[3], abs(b[2] - pc), abs(b[3] - pc)))
        out.append(sum(trs[-n:]) / n if i >= n - 1 else None)
    return out

def close_tr(p, px, why, t):
    p.update(x=round(px, 2), why=why, xt=t,
             r=round(((px - p["e"]) if p["d"] == "L" else (p["e"] - px)) / p["risk"], 2))

def run_day(day, A, c, open_m, live):
    ibe = open_m + c["ib_min"]
    ib = [b for _, b in day if b[5] < ibe]
    if not ib: return None
    H, L = max(b[2] for b in ib), min(b[3] for b in ib); M = (H + L) / 2
    k0 = next((k for k, (_, b) in enumerate(day) if b[5] >= ibe), None)
    res = {"H": H, "L": L, "M": round(M, 2), "trades": [], "pending": None, "ib_ok": k0 is not None}
    if k0 is None: return res
    pos, up, dn = None, False, False
    for k in range(k0, len(day)):
        gi, b = day[k]; pc = day[k - 1][1][4]
        up |= pc > H; dn |= pc < L
        if pos:
            if k >= pos["k"]:
                lg = pos["d"] == "L"
                hit_sl = b[3] <= pos["sl"] if lg else b[2] >= pos["sl"]
                hit_tp = b[2] >= pos["tp"] if lg else b[3] <= pos["tp"]
                if hit_sl: close_tr(pos, pos["sl"], "stop", b[0]); pos = None
                elif hit_tp: close_tr(pos, pos["tp"], "objetivo", b[0]); pos = None
            continue
        a = A[gi - 1] if gi else None
        if c["news"] and a and (b[2] - b[3]) > c["atr_mult"] * a: continue  # vela de noticias
        cl, sig = b[4], None
        if c["A"] and cl > H and pc <= H: sig = ("A", "L")
        elif c["A"] and cl < L and pc >= L: sig = ("A", "S")
        elif c["B"] and up and cl < H and pc >= H and cl > M: sig = ("B", "S")
        elif c["B"] and dn and cl > L and pc <= L and cl < M: sig = ("B", "L")
        elif c["C"] and b[2] >= H and cl < H and cl < b[1]: sig = ("C", "S")  # toca IB H y cierra roja dentro
        elif c["C"] and b[3] <= L and cl > L and cl > b[1]: sig = ("C", "L")  # toca IB L y cierra verde dentro
        if not sig or not c["long" if sig[1] == "L" else "short"]: continue
        st, d = sig
        nxt = day[k + 1][1] if k + 1 < len(day) else None
        if not nxt and not live: continue
        e = nxt[1] if nxt else cl
        if st == "A" and nxt and ((d == "L" and e < H + c["gap"]) or (d == "S" and e > L - c["gap"])):
            continue  # ruptura demasiado justa: la apertura no se separa lo suficiente del IB
        sl = b[3] if d == "L" else b[2]
        risk = (e - sl) if d == "L" else (sl - e)
        if risk <= 0: continue
        if st == "A":
            tp = e + c["rr"] * risk if d == "L" else e - c["rr"] * risk
        else:
            tp = M
            if (d == "L" and tp <= e) or (d == "S" and tp >= e): continue
        tr = {"st": st, "d": d, "sig_t": b[0], "e": round(e, 2), "sl": sl, "tp": round(tp, 2),
              "risk": round(risk, 2), "k": k + 1, "et": nxt[0] if nxt else None, "x": None}
        if nxt: res["trades"].append(tr); pos = tr
        else: res["pending"] = tr
    if pos and not live: close_tr(pos, day[-1][1][4], "cierre", day[-1][1][0])
    return res

SETUPS = {"A": "A · ruptura", "B": "B · fallo al 50%", "C": "C · rechazo al 50%"}

def hhmm(t): return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M")

def msg(s, r, t, pending):
    lg = t["d"] == "L"
    return (f"📊 SCANNER IB\n"
            f"{'🟢 COMPRA' if lg else '🔴 VENTA'} · {s['label']}\n"
            f"Setup {SETUPS[t['st']]}\n"
            f"Vela señal: {hhmm(t['sig_t'])} (Londres)\n"
            f"IB: H {r['H']} · 50% {r['M']} · L {r['L']}\n"
            f"Entrada: {'≈' if pending else ''}{t['e']} (apertura siguiente vela)\n"
            f"Stop: {t['sl']} · Objetivo: {t['tp']}\n"
            f"Riesgo: {t['risk']} pts"
            + (f"\n⚠️ Solo si la siguiente vela abre {'por encima de ' + str(round(r['H'] + CFG['gap'], 1)) if lg else 'por debajo de ' + str(round(r['L'] - CFG['gap'], 1))}"
               if pending and t["st"] == "A" else ""))

def send(text):
    if not (TOKEN and CHAT): print(text); return True
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                             data={"chat_id": CHAT, "text": text}, timeout=15).ok
    except Exception as e:
        print("Telegram:", e); return False

def main():
    try: state = json.load(open("state.json"))
    except Exception: state = {"sent": []}
    try: old = json.load(open("data.json"))["symbols"]
    except Exception: old = {}
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "cfg": CFG, "symbols": {}}
    for name, s in SYMBOLS.items():
        try: bars, days = load(s)
        except Exception as e:
            print(name, e)
            if name in old: out["symbols"][name] = old[name]
            continue
        if not bars:
            if name in old: out["symbols"][name] = old[name]
            continue
        out["symbols"][name] = {"label": s["label"], "open": hm(s["open"]), "close": hm(s["close"]),
                                "days": days, "bars": bars}
        if days[-1] != dt.datetime.now(ZoneInfo(s["tz"])).strftime("%Y-%m-%d"): continue
        A = atr(bars, CFG["atr_len"])
        day = [(gi, b) for gi, b in enumerate(bars) if b[6] == len(days) - 1]
        live = day[-1][1][5] < hm(s["close"]) - 5
        r = run_day(day, A, CFG, hm(s["open"]), live)
        if not r: continue
        items = [(t, False) for t in r["trades"]] + ([(r["pending"], True)] if r["pending"] else [])
        now_l = int((pd.Timestamp.now(tz="Europe/London").tz_localize(None) - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1))
        for t, p in items:
            sid = f"{name}|{t['sig_t']}|{t['st']}{t['d']}"
            if sid in state["sent"]: continue
            if now_l - (t["sig_t"] + 300) > MAX_AGE * 60:  # la vela de señal cerró hace demasiado
                print("Señal antigua, no se envía:", sid); state["sent"].append(sid); continue
            if send(msg(s, r, t, p)):
                state["sent"].append(sid)
    state["sent"] = state["sent"][-300:]
    json.dump(out, open("data.json", "w"), separators=(",", ":"))
    json.dump(state, open("state.json", "w"))

if __name__ == "__main__":
    main()
