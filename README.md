# Ubuntu:
1. System dependencies
   
sudo apt update
sudo apt install -y \
  python3 python3-pip python3-venv \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gir1.2-gst-plugins-base-1.0 gir1.2-gstreamer-1.0 \
  pipewire gstreamer1.0-pipewire \
  xdg-desktop-portal xdg-desktop-portal-gnome \
  python3-gi python3-dbus python3-numpy python3-serial

2. Add yourself to the dialout group (access to /dev/ttyUSB0)
   
sudo usermod -aG dialout $USER
LOG OUT and log back in (or reboot)

3. Start
   
python3 zielony.py

# Arch:
1. System dependencies
   
sudo pacman -S --needed \
  python python-pip \
  gstreamer gst-plugins-base gst-plugins-good \
  pipewire gst-plugin-pipewire \
  xdg-desktop-portal xdg-desktop-portal-gnome \
  python-gobject python-dbus python-numpy python-pyserial

2. Add yourself to the uucp group (Arch uses uucp instead of dialout)
   
sudo usermod -aG uucp $USER
LOG OUT and log back in (or reboot)

3. Start
   
python3 zielony.py
