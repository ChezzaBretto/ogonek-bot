import sys, os, time, hashlib, base64

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tt_core as core
from playwright.sync_api import sync_playwright

WAIT_MINUTES = 5
REFRESH_WORDS = ["обнов", "refresh", "истёк", "истек", "expired", "повтор"]

QR_TARGET = 1000
QR_MAX = 1280
QR_UPSCALE_JS = """
el => {
  const native = Math.max(el.width, el.height);
  if (!native) return null;
  let scale = Math.max(1, Math.round(__TARGET__ / native));
  while (scale > 1 && native * scale * 1.16 > __MAX__) scale--;
  const size = native * scale;
  // quiet-zone: у canvas её почти нет, а без белых полей сканеру тяжело
  const pad = Math.round(size * 0.08);
  const out = document.createElement('canvas');
  out.width = size + pad * 2;
  out.height = size + pad * 2;
  const g = out.getContext('2d');
  g.imageSmoothingEnabled = false;
  // canvas бывает с альфой, а Telegram пережимает фото в JPEG — прозрачность
  // там станет чёрной и QR инвертируется
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, out.width, out.height);
  g.drawImage(el, 0, 0, el.width, el.height, pad, pad, size, size);
  return out.toDataURL('image/png');
}
""".replace("__TARGET__", str(QR_TARGET)).replace("__MAX__", str(QR_MAX))

def emit(line):
    print(line, flush=True)

def grab_qr(canvas, path):
    data = None
    try:
        data = canvas.evaluate(QR_UPSCALE_JS)
    except Exception:
        pass
    if data and data.startswith("data:image/png;base64,"):
        raw = base64.b64decode(data.split(",", 1)[1])
        if len(raw) > 500:
            with open(path, "wb") as f:
                f.write(raw)
            return
    canvas.screenshot(path=path)

def qr_hash(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def main():
    if len(sys.argv) < 2:
        emit("FAIL нет user_dir")
        return
    user_dir = sys.argv[1]
    os.makedirs(user_dir, exist_ok=True)
    qr_path = os.path.join(user_dir, "qr.png")
    cookies_path = os.path.join(user_dir, "cookies.json")
    _lock = core.hold_browser_lock()

    with sync_playwright() as p:
        browser, ctx = core.new_context(p, scale=2)
        page = ctx.new_page()
        try:
            page.goto("https://www.tiktok.com/login/qrcode",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)

            last_hash = None
            deadline = time.time() + WAIT_MINUTES * 60

            while time.time() < deadline:
                try:
                    if any(c["name"] == "sessionid" for c in ctx.cookies()):
                        page.wait_for_timeout(3000)
                        cookies = core.normalize_cookies(ctx.cookies())
                        core.save_json(cookies_path, cookies)
                        os.chmod(cookies_path, 0o600)
                        emit("OK")
                        return
                except Exception:
                    pass

                try:
                    for btn in page.locator("button, div[role='button'], a").all()[:60]:
                        txt = (btn.inner_text() or "").strip().lower()
                        if txt and any(w in txt for w in REFRESH_WORDS) and btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(2500)
                            break
                except Exception:
                    pass

                try:
                    canvas = page.locator("canvas").first
                    if canvas.count() and canvas.is_visible():
                        grab_qr(canvas, qr_path)
                        h = qr_hash(qr_path)
                        if h != last_hash:
                            last_hash = h
                            emit("QR " + qr_path)
                except Exception:
                    pass

                page.wait_for_timeout(3000)

            emit("FAIL время вышло")
        except Exception as e:
            emit("FAIL {}".format(e))
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
