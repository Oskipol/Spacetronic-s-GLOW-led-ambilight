import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import dbus
import dbus.mainloop.glib
import numpy as np
import serial
import random
import time

# ===================== CONFIGURATION =====================
PORT = "/dev/ttyUSB0"
BAUD = 460800

#LED layout: U-shape (left bottom→top, top left→right, right top→bottom)
LEFT_LEDS = 24
TOP_LEDS = 48
RIGHT_LEDS = 24
TOTAL_LEDS = LEFT_LEDS + TOP_LEDS + RIGHT_LEDS  # 96

# How deep into the screen to sample (fraction of dimensions)
EDGE_DEPTH_FRAC = 0.10    # 10% of width/height

# Smoothing: 0.0 = immediate, closer to 1.0 = slower transitions
SMOOTH_FACTOR = 0.70

# Color correction
SATURATION_BOOST = 1.8    # >1 = livelier colors
GAMMA = 1.6               # lower gamma = brighter colors
BRIGHTNESS_FLOOR = 3      # pixels darker than this → black (kills noise)

# Target resolution for capture (small = faster)
CAPTURE_W = 320
CAPTURE_H = 180
TARGET_FPS = 30
# ========================================================

GAMMA_LUT = np.array(
    [int(((i / 255.0) ** GAMMA) * 255 + 0.5) for i in range(256)],
    dtype=np.uint8
)

prev_colors = np.zeros((TOTAL_LEDS, 3), dtype=np.float64)
last_send = 0
frame_count = 0

# Serial
def open_serial():
    """Otwórz port szeregowy z obsługą błędów."""
    while True:
        try:
            s = serial.Serial(PORT, BAUD, timeout=1)
            time.sleep(2) 
            print(f"Serial połączony: {PORT}")
            return s
        except (serial.SerialException, OSError) as e:
            print(f"Nie mogę otworzyć {PORT}: {e} — ponawiam za 3s...")
            time.sleep(3)

ser = open_serial()


def reconnect_serial():
    """Zamknij i ponownie otwórz port szeregowy."""
    global ser
    try:
        ser.close()
    except Exception:
        pass
    while True:
        try:
            ser = serial.Serial(PORT, BAUD, timeout=1)
            time.sleep(2)
            print(f"Serial ponownie połączony: {PORT}")
            return
        except (serial.SerialException, OSError) as e:
            print(f"Reconnect failed: {e} — ponawiam za 3s...")
            time.sleep(3)


# GStreamer + DBus
Gst.init(None)
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

portal = bus.get_object(
    "org.freedesktop.portal.Desktop",
    "/org/freedesktop/portal/desktop"
)
screencast = dbus.Interface(portal, "org.freedesktop.portal.ScreenCast")
loop = GLib.MainLoop()
session_handle = None
pipeline = None


# ===================== KOLORY =====================

def boost_saturation_vectorized(colors):
    """Wzmocnienie nasycenia w HSV — wektorowo przez numpy."""
    c = colors / 255.0
    cmax = c.max(axis=1)
    cmin = c.min(axis=1)
    delta = cmax - cmin

    # Hue
    hue = np.zeros(len(c))
    mask = delta > 0

    # R jest max
    r_max = mask & (c[:, 0] == cmax)
    hue[r_max] = (60 * ((c[r_max, 1] - c[r_max, 2]) / delta[r_max])) % 360
    # G jest max
    g_max = mask & (c[:, 1] == cmax)
    hue[g_max] = 60 * ((c[g_max, 2] - c[g_max, 0]) / delta[g_max]) + 120
    # B jest max
    b_max = mask & (c[:, 2] == cmax)
    hue[b_max] = 60 * ((c[b_max, 0] - c[b_max, 1]) / delta[b_max]) + 240

    hue = hue % 360

    # Saturation
    sat = np.where(cmax > 0, delta / cmax, 0)
    sat = np.clip(sat * SATURATION_BOOST, 0, 1)

    val = cmax

    # HSV → RGB
    h60 = hue / 60.0
    hi = np.floor(h60).astype(int) % 6
    f = h60 - np.floor(h60)
    p = val * (1 - sat)
    q = val * (1 - f * sat)
    t = val * (1 - (1 - f) * sat)

    result = np.zeros_like(c)
    for i_val in range(6):
        m = hi == i_val
        if i_val == 0:
            result[m] = np.column_stack([val[m], t[m], p[m]])
        elif i_val == 1:
            result[m] = np.column_stack([q[m], val[m], p[m]])
        elif i_val == 2:
            result[m] = np.column_stack([p[m], val[m], t[m]])
        elif i_val == 3:
            result[m] = np.column_stack([p[m], q[m], val[m]])
        elif i_val == 4:
            result[m] = np.column_stack([t[m], p[m], val[m]])
        elif i_val == 5:
            result[m] = np.column_stack([val[m], p[m], q[m]])

    return result * 255.0


# ===================== Screen Sampling =====================

def compute_led_colors(frame):
    """
    Mapuje krawędzie ekranu na LEDy w kształcie U:
      LED  0-23: lewa strona, dół → góra
      LED 24-71: góra, lewo → prawo
      LED 72-95: prawa strona, góra → dół
    """
    h, w = frame.shape[:2]
    edge_x = max(4, int(w * EDGE_DEPTH_FRAC))
    edge_y = max(4, int(h * EDGE_DEPTH_FRAC))

    colors = np.zeros((TOTAL_LEDS, 3), dtype=np.float64)
    idx = 0

    step = h / LEFT_LEDS
    for i in range(LEFT_LEDS):
        row = LEFT_LEDS - 1 - i
        y0 = max(0, int(row * step))
        y1 = min(h, int((row + 1) * step))
        region = frame[y0:y1, w - edge_x:w]
        colors[idx] = np.mean(region, axis=(0, 1)) if region.size > 0 else 0
        idx += 1
    step = w / TOP_LEDS
    for i in range(TOP_LEDS):
        col = TOP_LEDS - 1 - i
        x0 = max(0, int(col * step))
        x1 = min(w, int((col + 1) * step))
        region = frame[0:edge_y, x0:x1]
        colors[idx] = np.mean(region, axis=(0, 1)) if region.size > 0 else 0
        idx += 1

    step = h / RIGHT_LEDS
    for i in range(RIGHT_LEDS):
        y0 = max(0, int(i * step))
        y1 = min(h, int((i + 1) * step))
        region = frame[y0:y1, 0:edge_x]
        colors[idx] = np.mean(region, axis=(0, 1)) if region.size > 0 else 0
        idx += 1

    return colors


# ===================== PORTAL =====================

def handle_response(response, results):
    global session_handle

    if response != 0:
        print(f"Portal błąd: {response}")
        loop.quit()
        return

    if "session_handle" in results:
        session_handle = results["session_handle"]
        print(f"Sesja: {session_handle}")
        screencast.SelectSources(session_handle, {
            "types": dbus.UInt32(1),
            "handle_token": "amb" + str(random.randint(1000, 9999)),
        })

    elif "streams" in results:
        node_id = results["streams"][0][0]
        print(f"PipeWire node: {node_id}")
        start_gstreamer(node_id)

    else:
        print("Źródło wybrane, startuję...")
        screencast.Start(session_handle, "", {
            "handle_token": "amb" + str(random.randint(1000, 9999)),
        })


# ===================== GSTREAMER =====================

def start_gstreamer(pw_node_id):
    global pipeline

    pipe_str = (
        f"pipewiresrc path={pw_node_id} do-timestamp=true ! "
        f"videorate ! video/x-raw,framerate={TARGET_FPS}/1 ! "
        f"videoconvert ! video/x-raw,format=RGB ! "
        f"videoscale ! video/x-raw,width={CAPTURE_W},height={CAPTURE_H} ! "
        f"appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
    )

    pipeline = Gst.parse_launch(pipe_str)
    appsink = pipeline.get_by_name("sink")
    appsink.connect("new-sample", on_frame)

    gst_bus = pipeline.get_bus()
    gst_bus.add_signal_watch()
    gst_bus.connect("message::error", lambda _, m: (
        print(f"GStreamer error: {m.parse_error()[0].message}"), loop.quit()))
    gst_bus.connect("message::eos", lambda _, m: (
        print("Koniec streamu"), loop.quit()))

    pipeline.set_state(Gst.State.PLAYING)
    print("Pipeline uruchomiony")


def on_frame(sink):
    global prev_colors, frame_count, last_send

    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    now = time.monotonic()
    interval = 1.0 / TARGET_FPS
    if now - last_send < interval:
        return Gst.FlowReturn.OK
    last_send = now

    caps = sample.get_caps()
    struct = caps.get_structure(0)
    width = struct.get_value("width")
    height = struct.get_value("height")

    buf = sample.get_buffer()
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        return Gst.FlowReturn.ERROR

    try:
        data = np.frombuffer(mapinfo.data, dtype=np.uint8)
        expected = height * width * 3
        if data.size < expected:
            return Gst.FlowReturn.OK

        stride = data.size // height
        if stride != width * 3:
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            for y in range(height):
                s = y * stride
                frame[y] = data[s:s + width * 3].reshape(width, 3)
        else:
            frame = data[:expected].reshape((height, width, 3))

        raw_colors = compute_led_colors(frame)

        dark = np.all(raw_colors < BRIGHTNESS_FLOOR, axis=1)
        raw_colors[dark] = 0

        enhanced = boost_saturation_vectorized(raw_colors)

        prev_colors = prev_colors * SMOOTH_FACTOR + enhanced * (1.0 - SMOOTH_FACTOR)
        output = np.clip(prev_colors, 0, 255).astype(np.uint8)

        output = GAMMA_LUT[output]

        header = bytes([0xAA, 0x55, 0x01, 0x01, 0x21, 0x00])
        footer = bytes([0x0D, 0x0A])

        data_out = bytearray(header)
        for r, g, b in output:
            data_out.extend([r, g, b])  
        data_out.extend(footer)

        try:
            ser.write(data_out)
            ser.flush()
        except (serial.SerialException, OSError) as e:
            print(f"Serial błąd zapisu: {e} — próbuję reconnect...")
            reconnect_serial()

        frame_count += 1
        if frame_count <= 3:
            print(f"Frame {frame_count}: "
                  f"L[0]={output[0]} T[24]={output[24]} R[72]={output[72]}")

    finally:
        buf.unmap(mapinfo)

    return Gst.FlowReturn.OK


# ===================== START =====================
print(f"Ambilight: {TOTAL_LEDS} LEDs  L:{LEFT_LEDS} T:{TOP_LEDS} R:{RIGHT_LEDS}")
print(f"Próbkowanie: {EDGE_DEPTH_FRAC*100:.0f}% krawędzi, {CAPTURE_W}x{CAPTURE_H} @ {TARGET_FPS}fps")
print(f"Smooth={SMOOTH_FACTOR}  Saturation={SATURATION_BOOST}x  Gamma={GAMMA}")

handle = screencast.CreateSession({
    "handle_token": "amb" + str(random.randint(1000, 9999)),
    "session_handle_token": "amb" + str(random.randint(1000, 9999)),
})

bus.add_signal_receiver(
    handle_response,
    dbus_interface="org.freedesktop.portal.Request",
    signal_name="Response"
)

loop.run()
