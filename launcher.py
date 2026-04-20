import os
import json
import subprocess
import urllib.request
import re
import customtkinter as ctk
import threading
from datetime import datetime
import ctypes

myappid = "plauncher.app.v1"  # any unique string
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)


# ---------------- PATHS ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "launcher_profiles.json")
MC_DIR = os.path.join(os.getenv("APPDATA"), ".minecraft")

LOG_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "plauncher_logs")
os.makedirs(LOG_DIR, exist_ok=True)

selected_profile = None


# ---------------- FIRST RUN SETUP ----------------
def ensure_mc_dirs():
    os.makedirs(MC_DIR, exist_ok=True)
    os.makedirs(os.path.join(MC_DIR, "versions"), exist_ok=True)
    os.makedirs(os.path.join(MC_DIR, "libraries"), exist_ok=True)
    os.makedirs(os.path.join(MC_DIR, "assets"), exist_ok=True)


# ---------------- PROFILES ----------------
def load_profiles():
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_profiles(data):
    with open(PROFILE_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ---------------- JAVA ----------------
def find_java():
    try:
        result = subprocess.run(["where", "java"], capture_output=True, text=True)
        for path in result.stdout.split("\n"):
            path = path.strip()
            try:
                p = subprocess.run([path, "-version"], capture_output=True, text=True)
                out = p.stderr + p.stdout
                m = re.search(r'version "(\d+)', out)
                if m and int(m.group(1)) >= 17:
                    return path
            except:
                pass
    except:
        pass
    return None


# ---------------- VERSION ----------------
def load_version(version):
    path = os.path.join(MC_DIR, "versions", version, f"{version}.json")
    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        data = json.load(f)

    if "inheritsFrom" in data:
        parent = load_version(data["inheritsFrom"])
        if parent:
            data["libraries"] = parent.get("libraries", []) + data.get("libraries", [])
            data.setdefault("mainClass", parent.get("mainClass"))
            data.setdefault("assets", parent.get("assets"))

    return data


def is_allowed(lib):
    rules = lib.get("rules")
    if not rules:
        return True
    for r in rules:
        if r.get("action") == "allow":
            osr = r.get("os")
            if not osr or osr.get("name") == "windows":
                return True
    return False


def maven_to_path(name):
    parts = name.split(":")
    group = parts[0].replace(".", "/")
    artifact = parts[1]
    version = parts[2]

    file = f"{artifact}-{version}.jar"
    if len(parts) == 4:
        file = f"{artifact}-{version}-{parts[3]}.jar"

    return f"{group}/{artifact}/{version}/{file}"


def ensure_libraries(data):
    libs_dir = os.path.join(MC_DIR, "libraries")

    for lib in data.get("libraries", []):
        if not is_allowed(lib):
            continue

        name = lib.get("name")
        if not name:
            continue

        path = maven_to_path(name)
        full = os.path.join(libs_dir, path)

        if not os.path.exists(full):
            art = lib.get("downloads", {}).get("artifact")
            if art and "url" in art:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                urllib.request.urlretrieve(art["url"], full)


def build_classpath(data, version):
    libs_dir = os.path.join(MC_DIR, "libraries")
    cp = []

    for lib in data.get("libraries", []):
        if not is_allowed(lib):
            continue

        name = lib.get("name")
        if not name:
            continue

        full = os.path.join(libs_dir, maven_to_path(name))
        if os.path.exists(full):
            cp.append(full)

    cp.append(os.path.join(MC_DIR, "versions", version, f"{version}.jar"))
    return ";".join(cp)


# ---------------- DOWNLOAD VERSION ----------------
VERSION_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest.json"

def get_versions():
    try:
        with urllib.request.urlopen(VERSION_MANIFEST) as r:
            data = json.load(r)
            return [v["id"] for v in data["versions"] if v["type"] == "release"][:50]
    except:
        return []

def download_version(version, app=None):
    try:
        # 1. Get manifest
        with urllib.request.urlopen(VERSION_MANIFEST) as r:
            manifest = json.load(r)

        # 2. Find version entry
        info = next((v for v in manifest["versions"] if v["id"] == version), None)
        if not info:
            if app:
                app.info.configure(text="Version not found")
            return False

        # 3. Download version JSON
        with urllib.request.urlopen(info["url"]) as r:
            data = json.load(r)

        folder = os.path.join(MC_DIR, "versions", version)
        os.makedirs(folder, exist_ok=True)

        json_path = os.path.join(folder, f"{version}.json")
        with open(json_path, "w") as f:
            json.dump(data, f, indent=4)

        # 4. Download client JAR
        jar_url = data["downloads"]["client"]["url"]
        jar_path = os.path.join(folder, f"{version}.jar")

        urllib.request.urlretrieve(jar_url, jar_path)

        # 5. Auto-register as installation (IMPORTANT FIX)
        if app:
            profiles = load_profiles()
            profile = profiles[selected_profile]

            version = profile.get("version")
            loader = profile.get("loader", "vanilla")
			
            if hasattr(app, "load_profiles_ui"):
                app.load_profiles_ui()

            app.info.configure(text=f"Downloaded {version}")

        return True

    except Exception as e:
        if app:
            app.info.configure(text=f"Download failed: {e}")
        return False


# ---------------- LAUNCH ----------------
def launch(app):
    global selected_profile

    if not selected_profile:
        app.info.configure(text="Select installation first")
        return

    profiles = load_profiles()
    version = profiles[selected_profile]

    data = load_version(version)
    java = find_java()

    ensure_libraries(data)
    cp = build_classpath(data, version)

    cmd = [
        java,
        "-Xmx2G",
        "-cp",
        cp,
        data["mainClass"],
        "--username", app.player.get() or "Player",
        "--version", version,
        "--gameDir", MC_DIR,
        "--assetsDir", os.path.join(MC_DIR, "assets"),
        "--assetIndex", data.get("assets", ""),
        "--uuid", "1234",
        "--accessToken", "0",
        "--userType", "legacy"
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    log_file = None
    if app.save_logs.get():
        name = f"log_{datetime.now().strftime('%H-%M-%S')}.txt"
        log_file = open(os.path.join(LOG_DIR, name), "w", encoding="utf-8")

    def stream():
        for line in process.stdout:
            if app.show_logs.get():
                app.log_box.insert("end", line)
                app.log_box.see("end")
            if log_file:
                log_file.write(line)
        if log_file:
            log_file.close()

    threading.Thread(target=stream, daemon=True).start()

    if app.close_on_launch.get():
        app.after(500, app.destroy)

    app.info.configure(text="Launching...")


# ---------------- UI ----------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class Launcher(ctk.CTk):
    def __init__(self):
        super().__init__()   # ← THIS MUST COME FIRST

        self.title("PLauncher")

        from PIL import Image, ImageTk

        icon_path = os.path.join(BASE_DIR, "PLauncher.ico")
        if os.path.exists(icon_path):
            icon = ImageTk.PhotoImage(Image.open(icon_path))
            self.iconphoto(True, icon)
	


        self.geometry("1200x700")
        self.grid_columnconfigure(0, weight=0, minsize=260)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.show_logs = ctk.BooleanVar(value=False)
        self.save_logs = ctk.BooleanVar(value=False)
        self.close_on_launch = ctk.BooleanVar(value=True)

        # LEFT
        self.left = ctk.CTkFrame(self)
        self.left.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(self.left, text="PLauncher",
                     text_color="#2f81f7", font=("Arial", 22)).pack(pady=5)

        self.player = ctk.CTkEntry(self.left, placeholder_text="Player name")
        self.player.pack(fill="x", padx=10, pady=5)

        self.install_list = ctk.CTkScrollableFrame(self.left, label_text="Installations")
        self.install_list.pack(fill="both", expand=True)

        self.buttons = {}

        # CENTER (LOGS)
        self.center = ctk.CTkFrame(self)
        self.center.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)

        self.log_box = ctk.CTkTextbox(self.center)
        self.log_box.pack(fill="both", expand=True)

        # RIGHT (SETTINGS)
        self.right_container = ctk.CTkFrame(self)
        self.right_container.grid(row=1, column=2, sticky="nsew", padx=10, pady=10)

        self.settings_frame = ctk.CTkFrame(self.right_container)
        self.settings_frame.pack(fill="both", expand=True)

        ctk.CTkLabel(self.settings_frame, text="Settings").pack(pady=10)

        ctk.CTkCheckBox(self.settings_frame, text="Close after launch",
                        variable=self.close_on_launch).pack()

        ctk.CTkCheckBox(self.settings_frame, text="Show logs",
                        variable=self.show_logs,
                        command=self.update_layout).pack()

        ctk.CTkCheckBox(self.settings_frame, text="Save logs",
                        variable=self.save_logs).pack()

        # NEW INSTALL BUTTON
        ctk.CTkButton(self.settings_frame, text="+ New Installation",
                      command=self.open_install_menu).pack(pady=10)

        # INSTALL MENU
        self.install_frame = ctk.CTkFrame(self.right_container)

        ctk.CTkLabel(self.install_frame, text="New Installation").pack(pady=10)

        self.install_name = ctk.CTkEntry(self.install_frame, placeholder_text="Name")
        self.install_name.pack(fill="x", padx=10, pady=5)

        self.version_select = ctk.CTkComboBox(self.install_frame, values=[])
        self.version_select.pack(fill="x", padx=10, pady=5)

        ctk.CTkButton(self.install_frame, text="Download Version",
                      command=self.download_selected).pack()

        ctk.CTkButton(self.install_frame, text="Save",
                      command=self.save_install).pack(pady=10)

        ctk.CTkButton(self.install_frame, text="Back",
                      command=self.close_install_menu).pack()

        # PLAY
        self.play = ctk.CTkButton(self, text="PLAY", height=60,
                                   command=lambda: launch(self))
        self.play.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)

        self.info = ctk.CTkLabel(self, text="Idle")
        self.info.grid(row=2, column=1, columnspan=2)

        self.load()
        self.update_layout()

    # ---------------- VERSION POPUP ----------------
    def open_version_popup(self):
        popup = ctk.CTkToplevel(self)
        popup.geometry("300x450")
        popup.title("Select Version")
        popup.grab_set()

        ctk.CTkLabel(popup, text="Available Versions").pack(pady=10)

        versions = get_versions()

        scroll = ctk.CTkScrollableFrame(popup)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        def start_download(v):
            popup.destroy()
            self.info.configure(text=f"Downloading {v}...")
            threading.Thread(target=download_version, args=(v, self), daemon=True).start()

        for v in versions:
            ctk.CTkButton(
                scroll,
                text=v,
                fg_color="#161b22",
                hover_color="#2f81f7",
                command=lambda ver=v: start_download(ver)
            ).pack(fill="x", pady=3)

    # ---------------- BUTTON HANDLER ----------------
    def download_selected(self):
        self.open_version_popup()

    # ---------------- UI TOGGLE ----------------
    def update_layout(self):
        if self.show_logs.get():
            self.center.grid()
            self.grid_columnconfigure(1, weight=1)
            self.grid_columnconfigure(2, weight=0)
        else:
            self.center.grid_remove()
            self.grid_columnconfigure(1, weight=0)
            self.grid_columnconfigure(2, weight=1)

    # ---------------- INSTALLS ----------------
    def load(self):
        profiles = load_profiles()

        for name in profiles:
            b = ctk.CTkButton(
                self.install_list,
                text=name,
                fg_color="#161b22",
                command=lambda n=name: self.select(n)
            )
            b.pack(fill="x", pady=5)
            self.buttons[name] = b

    def select(self, name):
        global selected_profile
        selected_profile = name

        for b in self.buttons.values():
            b.configure(fg_color="#161b22")

        self.buttons[name].configure(fg_color="#2f81f7")
        self.info.configure(text=f"Selected: {name}")

    def open_install_menu(self):
        self.settings_frame.pack_forget()
        self.install_frame.pack(fill="both", expand=True)

        versions_path = os.path.join(MC_DIR, "versions")
        if os.path.exists(versions_path):
            versions = [
                v for v in os.listdir(versions_path)
                if os.path.isdir(os.path.join(versions_path, v))
            ]
            self.version_select.configure(values=versions)

    def close_install_menu(self):
        self.install_frame.pack_forget()
        self.settings_frame.pack(fill="both", expand=True)

    def save_install(self):
        name = self.install_name.get()
        version = self.version_select.get()

        if not name or not version:
            self.info.configure(text="Fill all fields")
            return

        profiles = load_profiles()
        profiles[name] = version
        save_profiles(profiles)

        self.load()
        self.close_install_menu()
        self.info.configure(text=f"Created: {name}")


# ---------------- RUN ----------------
app = Launcher()
app.mainloop()
