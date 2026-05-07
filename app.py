import os
import subprocess
import shutil
import threading
import time
import socket
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

REPO_URL = os.environ.get("REPO_URL", "https://github.com/sherlockes/sherlockes.github.io.git")
REPO_DIR = "/site/repo"
LOGS_BUFFER = []
MAX_LOGS = 300
hugo_proc = None
proc_lock = threading.Lock()

# Keep track of the last known client host IP to use on automatic restarts
last_host_ip = "localhost"

# Combined configuration flag for drafts and future articles
build_drafts_and_future = True

# Global dictionary to store the latest parsed build metrics
build_metrics = {
    "pages": "N/A",
    "static_files": "N/A",
    "build_time": "N/A"
}

# Auto-shutdown on inactivity (15 minutes of no API polling/tab activity)
last_activity_time = time.time()
INACTIVITY_TIMEOUT = 900 # 15 minutes in seconds

def update_activity():
    global last_activity_time
    last_activity_time = time.time()

def inactivity_checker():
    global last_activity_time, hugo_proc
    while True:
        time.sleep(30) # Check every 30 seconds
        if hugo_proc and hugo_proc.poll() is None:
            idle_time = time.time() - last_activity_time
            if idle_time > INACTIVITY_TIMEOUT:
                add_log(f"⏰ Auto-apagado por inactividad ({int(idle_time // 60)} minutos sin recibir peticiones de la pestaña).")
                with proc_lock:
                    stop_hugo_internal()

def get_recent_edited_files():
    """Scans repo content folder for the 3 most recently modified markdown files and generates their Hugo URLs."""
    if not os.path.exists(REPO_DIR):
        return []
    
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return []
    
    files_list = []
    try:
        for root, dirs, files in os.walk(content_dir):
            for file in files:
                if file.endswith(".md"):
                    path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(path)
                        rel_path = os.path.relpath(path, content_dir)
                        files_list.append((rel_path, mtime))
                    except:
                        pass
    except Exception as e:
        return []
                        
    # Sort files by modified time desc
    files_list.sort(key=lambda x: x[1], reverse=True)
    
    recent_files = []
    for rel_path, _ in files_list[:3]:
        # Generate clean URL path: "posts/mi-post.md" -> "/posts/mi-post/"
        clean_path = rel_path.rsplit(".", 1)[0]
        
        # Handle index / _index files
        if clean_path.endswith("/index") or clean_path == "index":
            clean_path = clean_path.rsplit("/index", 1)[0]
        elif clean_path.endswith("/_index") or clean_path == "_index":
            clean_path = clean_path.rsplit("/_index", 1)[0]
            
        if clean_path == "" or clean_path == "index":
            url_path = "/"
        else:
            url_path = f"/{clean_path}/"
            
        # Capitalize and format display name
        display_name = rel_path.split("/")[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").capitalize()
        recent_files.append({
            "name": display_name,
            "url": url_path
        })
    return recent_files

def get_file_from_url_path(url_path):
    """Maps a Hugo URL path back to the actual markdown file in REPO_DIR."""
    import re
    import urllib.parse
    
    # Decode URL-encoded characters (like %C3%B3 to ó)
    url_path = urllib.parse.unquote(url_path)
    
    if not os.path.exists(REPO_DIR):
        return None
        
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return None

    cleaned_path = url_path.strip("/")
    if not cleaned_path:
        return None

    # Use the last component of the path for matching (e.g. /post/emacs-desde-cero/ -> emacs-desde-cero)
    last_segment = cleaned_path.split("/")[-1]

    # Helper to slugify text
    def slugify(text):
        text = text.lower()
        # Simple accent removal
        accents = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
        for k, v in accents.items():
            text = text.replace(k, v)
        text = re.sub(r'[^a-z0-9\s.-]', '', text)
        text = re.sub(r'[\s_]+', '-', text)
        text = re.sub(r'-+', '-', text)
        return text.strip('-')

    target_slug = slugify(last_segment)

    # Walk through the content directory to find a match
    for root, _, files in os.walk(content_dir):
        for file in files:
            if file.endswith((".md", ".html")):
                full_path = os.path.join(root, file)
                
                # Check 1: Filename match (ignoring date prefix and extension)
                base_name = file.rsplit(".", 1)[0]
                # Remove common date formats like YYYYMMDD_ or YYYY-MM-DD-
                clean_base = re.sub(r'^\d{8}_', '', base_name)
                clean_base = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', clean_base)
                if slugify(clean_base) == target_slug:
                    return full_path
                    
                # Check 2: Parse front matter of the file
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        lines = []
                        # Only read first 30 lines (usually contains front matter)
                        for _ in range(30):
                            line = f.readline()
                            if not line:
                                break
                            lines.append(line)
                        
                        front_matter_str = "".join(lines)
                        
                        # Check slug:
                        slug_match = re.search(r'^slug:\s*["\']?([^"\n\']+)["\']?', front_matter_str, re.MULTILINE | re.IGNORECASE)
                        if slug_match and slugify(slug_match.group(1)) == target_slug:
                            return full_path
                            
                        # Check url:
                        url_match = re.search(r'^url:\s*["\']?([^"\n\']+)["\']?', front_matter_str, re.MULTILINE | re.IGNORECASE)
                        if url_match and slugify(url_match.group(1)) == target_slug:
                            return full_path
                            
                        # Check title:
                        title_match = re.search(r'^title:\s*["\']?([^"\n\']+)["\']?', front_matter_str, re.MULTILINE | re.IGNORECASE)
                        if title_match and slugify(title_match.group(1)) == target_slug:
                            return full_path
                except Exception as e:
                    pass

    return None

@app.route("/api/edit-info", methods=["GET"])
def get_edit_info():
    update_activity()
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"editable": False, "error": "No path provided"}), 400
        
    file_path = get_file_from_url_path(path)
    if not file_path:
        return jsonify({"editable": False})
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({
            "editable": True,
            "file_path": os.path.relpath(file_path, REPO_DIR),
            "content": content
        })
    except Exception as e:
        return jsonify({"editable": False, "error": str(e)}), 500

@app.route("/api/save-file", methods=["POST"])
def save_file():
    update_activity()
    data = request.json or {}
    rel_file_path = data.get("file_path", "").strip()
    content = data.get("content", "")
    
    if not rel_file_path:
        return jsonify({"status": "error", "message": "No file path provided"}), 400
        
    # Ensure it's safe and doesn't escape the REPO_DIR
    full_path = os.path.abspath(os.path.join(REPO_DIR, rel_file_path))
    if not full_path.startswith(os.path.abspath(REPO_DIR)):
        return jsonify({"status": "error", "message": "Access denied"}), 403
        
    if not os.path.exists(full_path):
        return jsonify({"status": "error", "message": "File not found"}), 404
        
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        add_log(f"📝 Artículo modificado y guardado en disco: {rel_file_path}")
        return jsonify({"status": "success", "message": "Artículo guardado con éxito."})
    except Exception as e:
        add_log(f"❌ Error al guardar artículo: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def get_existing_taxonomies():
    """Walks through the content directory and extracts unique categories and tags."""
    categories = set()
    tags = set()
    
    if not os.path.exists(REPO_DIR):
        return {"categories": [], "tags": []}
        
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return {"categories": [], "tags": []}
        
    import re
    
    for root, _, files in os.walk(content_dir):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        # Read the front matter (usually first 1500 chars)
                        head = f.read(1500)
                        
                    fm_match = re.search(r'^---\s*\n(.*?)\n---', head, re.DOTALL)
                    if fm_match:
                        yaml_content = fm_match.group(1)
                        lines = yaml_content.split("\n")
                        
                        in_categories = False
                        in_tags = False
                        
                        for line in lines:
                            stripped = line.strip()
                            if not stripped:
                                continue
                                
                            # Check if we transition to a different key
                            if ":" in stripped and not stripped.startswith("-"):
                                in_categories = False
                                in_tags = False
                                
                                key, _, val = stripped.partition(":")
                                key = key.strip().lower()
                                val = val.strip()
                                
                                if key == "categories":
                                    if val:
                                        if val.startswith("[") and val.endswith("]"):
                                            items = [item.strip().strip('"').strip("'") for item in val[1:-1].split(",") if item.strip()]
                                            categories.update(items)
                                        else:
                                            categories.add(val.strip('"').strip("'"))
                                    else:
                                        in_categories = True
                                elif key == "tags":
                                    if val:
                                        if val.startswith("[") and val.endswith("]"):
                                            items = [item.strip().strip('"').strip("'") for item in val[1:-1].split(",") if item.strip()]
                                            tags.update(items)
                                        else:
                                            tags.add(val.strip('"').strip("'"))
                                    else:
                                        in_tags = True
                                        
                            elif stripped.startswith("-") and (in_categories or in_tags):
                                val = stripped[1:].strip().strip('"').strip("'")
                                if val:
                                    if in_categories:
                                        categories.add(val)
                                    elif in_tags:
                                        tags.add(val)
                except Exception:
                    pass
                    
    return {
        "categories": sorted(list(c for c in categories if c)),
        "tags": sorted(list(t for t in tags if t))
    }

@app.route("/api/taxonomies", methods=["GET"])
def get_taxonomies():
    update_activity()
    return jsonify(get_existing_taxonomies())

@app.route("/api/new-draft", methods=["POST"])
def new_draft():
    update_activity()
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"status": "error", "message": "El título es obligatorio."}), 400
        
    categories = data.get("categories", ["computing"])
    tags = data.get("tags", ["blog"])
    
    # If empty lists are provided, use defaults
    if not categories:
        categories = ["computing"]
    if not tags:
        tags = ["blog"]
        
    import datetime
    import re
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    iso_date = datetime.datetime.now().astimezone().isoformat()
    
    # Simple slugify helper for filename and URL path
    def slugify_simple(text):
        text = text.lower()
        accents = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
        for k, v in accents.items():
            text = text.replace(k, v)
        text = re.sub(r'[^a-z0-9\s.-]', '', text)
        text = re.sub(r'[\s_]+', '-', text)
        text = re.sub(r'-+', '-', text)
        return text.strip('-')
        
    slug = slugify_simple(title)
    
    # Filename format: YYYYMMDD_slug_with_underscores.md
    filename_slug = slug.replace("-", "_")
    filename = f"{date_str}_{filename_slug}.md"
    
    # Ensure post directory exists
    post_dir = os.path.join(REPO_DIR, "content", "post")
    os.makedirs(post_dir, exist_ok=True)
    
    full_path = os.path.join(post_dir, filename)
    
    # Prevent overwriting if file already exists
    counter = 1
    while os.path.exists(full_path):
        filename = f"{date_str}_{filename_slug}_{counter}.md"
        full_path = os.path.join(post_dir, filename)
        counter += 1
        
    today_iso = today.isoformat()
    thumbnail_path = f"images/{date_str}_{filename_slug}_00.jpg"
    
    categories_yaml = "\n".join([f'- "{cat}"' for cat in categories])
    tags_yaml = "\n".join([f'- "{tag}"' for tag in tags])
    
    # Read custom template if exists, else fallback to default template
    template_path = "new_template.md"
    template_content = ""
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
        except Exception as e:
            add_log(f"⚠️ Error al leer new_template.md: {e}")
            
    if template_content:
        default_content = template_content.replace("{title}", title)\
                                          .replace("{date}", today_iso)\
                                          .replace("{creation}", today_iso)\
                                          .replace("{thumbnail}", thumbnail_path)\
                                          .replace("{categories}", categories_yaml)\
                                          .replace("{tags}", tags_yaml)
    else:
        default_content = f"""---
title: "{title}"
date: "{today_iso}"
creation: "{today_iso}"
description: "He creado {title} para compartir mis opiniones y conocimientos."
thumbnail: "{thumbnail_path}"
disable_comments: true
authorbox: false
toc: false
mathjax: false
categories:
{categories_yaml}
tags: 
{tags_yaml}
draft: true
weight: 5
---

Escribe aquí el contenido de tu nuevo artículo...
"""
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(default_content)
            
        add_log(f"🆕 Creado nuevo borrador: {filename}")
        
        # Relative path from REPO_DIR
        rel_path = os.path.relpath(full_path, REPO_DIR)
        
        return jsonify({
            "status": "success",
            "file_path": rel_path,
            "url_path": f"/{slug}/",
            "content": default_content
        })
    except Exception as e:
        add_log(f"❌ Error al crear nuevo borrador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/drafts", methods=["GET"])
def get_drafts():
    update_activity()
    if not os.path.exists(REPO_DIR):
        return jsonify([])
        
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return jsonify([])

    import datetime
    import re
    today_str = datetime.date.today().isoformat()
    
    drafts = []
    for root, _, files in os.walk(content_dir):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        head = f.read(1500)
                    
                    match = re.search(r'^---\s*\n(.*?)\n---', head, re.DOTALL)
                    if match:
                        yaml_content = match.group(1)
                        is_draft = False
                        title = file
                        date_val = ""
                        
                        for line in yaml_content.split("\n"):
                            line = line.strip()
                            if line.startswith("draft:"):
                                val = line.split(":", 1)[1].strip().lower()
                                is_draft = (val == "true")
                            elif line.startswith("title:"):
                                title_val = line.split(":", 1)[1].strip()
                                if title_val.startswith(('"', "'")) and title_val.endswith(('"', "'")):
                                    title_val = title_val[1:-1]
                                title = title_val
                            elif line.startswith("date:"):
                                date_val = line.split(":", 1)[1].strip().replace('"', '').replace("'", "")
                        
                        is_future = False
                        if date_val:
                            date_match = re.match(r'^(\d{4}-\d{2}-\d{2})', date_val)
                            if date_match:
                                file_date = date_match.group(1)
                                if file_date > today_str:
                                    is_future = True
                        
                        if is_draft or is_future:
                            def slugify_py(text):
                                text = text.lower()
                                accents = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
                                for k, v in accents.items():
                                    text = text.replace(k, v)
                                text = re.sub(r'[^a-z0-9\s.-]', '', text)
                                text = re.sub(r'[\s_]+', '-', text)
                                text = re.sub(r'-+', '-', text)
                                return text.strip('-')
                                
                            slug = slugify_py(title)
                            url_path = f"/{slug}/"
                            
                            drafts.append({
                                "title": title,
                                "file_path": os.path.relpath(full_path, REPO_DIR),
                                "url_path": url_path,
                                "is_draft": is_draft,
                                "is_future": is_future,
                                "date": date_val
                            })
                except Exception as e:
                    pass
                    
    drafts.sort(key=lambda x: x.get("date", "") or "", reverse=True)
    return jsonify(drafts)

def add_log(message):
    global LOGS_BUFFER
    timestamp = time.strftime("%H:%M:%S")
    LOGS_BUFFER.append(f"[{timestamp}] {message}")
    if len(LOGS_BUFFER) > MAX_LOGS:
        LOGS_BUFFER.pop(0)

def log_reader(proc):
    global build_metrics
    try:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            line_str = line.strip()
            add_log(line_str)
            
            # Robust parsing of Hugo's build statistics table & build time
            if "Pages" in line_str and "│" in line_str:
                parts = line_str.split("│")
                if len(parts) > 1:
                    build_metrics["pages"] = parts[1].strip()
            elif "Static files" in line_str and "│" in line_str:
                parts = line_str.split("│")
                if len(parts) > 1:
                    build_metrics["static_files"] = parts[1].strip()
            elif "Built in" in line_str:
                build_metrics["build_time"] = line_str.replace("Built in", "").strip()
        proc.stdout.close()
    except Exception as e:
        add_log(f"Error reading logs: {e}")

def run_clone_and_start(host_ip=None):
    global hugo_proc, build_metrics
    with proc_lock:
        if hugo_proc and hugo_proc.poll() is None:
            add_log("Hugo is already running. Stopping it first...")
            stop_hugo_internal()
        
        # Reset metrics on clean start
        build_metrics = {
            "pages": "N/A",
            "static_files": "N/A",
            "build_time": "N/A"
        }
        
        add_log("🧹 Cleaning old repository content (rm -rf)...")
        try:
            # Using standard rm -rf is 100x more robust than shutil.rmtree on read-only git files
            subprocess.run(["rm", "-rf", REPO_DIR], capture_output=True)
            os.makedirs(REPO_DIR, exist_ok=True)
            add_log("Clean completed successfully.")
        except Exception as e:
            add_log(f"Error cleaning repo: {e}")

        add_log(f"📥 Cloning repository from {REPO_URL}...")
        try:
            # Set GIT_TERMINAL_PROMPT=0 to prevent hanging on authentication prompts
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            res = subprocess.run(["git", "clone", REPO_URL, REPO_DIR], capture_output=True, text=True, env=env)
            if res.returncode != 0:
                add_log(f"Git clone failed: {res.stderr.strip() or res.stdout.strip()}")
                return
            add_log("Git clone completed successfully.")
        except Exception as e:
            add_log(f"Git clone error: {e}")
            return

        start_hugo_internal(host_ip)

def start_hugo_internal(host_ip=None):
    global hugo_proc, build_drafts_and_future, last_host_ip, build_metrics
    if not os.path.exists(REPO_DIR):
        add_log("Repo directory does not exist. Please run a full start.")
        return False
    
    if host_ip:
        last_host_ip = host_ip
    else:
        host_ip = last_host_ip
    
    # Reset metrics before starting up
    build_metrics = {
        "pages": "N/A",
        "static_files": "N/A",
        "build_time": "N/A"
    }
    
    add_log(f"🚀 Launching Hugo Server (Borradores y Futuros: {'SÍ' if build_drafts_and_future else 'NO'}) on baseURL http://{host_ip}:1313...")
    try:
        cmd = [
            "hugo", "server",
            "--disableFastRender",
            "--bind", "0.0.0.0",
            "--baseURL", f"http://{host_ip}:1313",
            "--navigateToChanged"
        ]
        if build_drafts_and_future:
            cmd.append("--buildDrafts")
            cmd.append("--buildFuture")

        hugo_proc = subprocess.Popen(
            cmd,
            cwd=REPO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        threading.Thread(target=log_reader, args=(hugo_proc,), daemon=True).start()
        add_log("Hugo server process started.")
        return True
    except Exception as e:
        add_log(f"Failed to start Hugo: {e}")
        return False

def stop_hugo_internal():
    global hugo_proc, build_metrics
    # Reset metrics on stop
    build_metrics = {
        "pages": "N/A",
        "static_files": "N/A",
        "build_time": "N/A"
    }
    if hugo_proc and hugo_proc.poll() is None:
        add_log("Terminating Hugo process...")
        hugo_proc.terminate()
        try:
            hugo_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            add_log("Force-killing Hugo process...")
            hugo_proc.kill()
        add_log("Hugo process stopped.")
        hugo_proc = None
    else:
        # Fallback to pkill just in case of orphan processes
        subprocess.run(["pkill", "-f", "hugo"], capture_output=True)
        add_log("Stopped any running Hugo instance.")
        hugo_proc = None

@app.route("/")
def index():
    update_activity()
    return render_template("index.html")

@app.route("/api/config", methods=["GET", "POST"])
def config():
    global build_drafts_and_future, last_host_ip
    update_activity()
    if request.method == "POST":
        data = request.json or {}
        build_drafts_and_future = data.get("build_drafts_and_future", build_drafts_and_future)
        
        # Keep track of client host
        host = request.headers.get("Host", last_host_ip)
        last_host_ip = host.split(":")[0] if ":" in host else host
        
        add_log(f"🔧 Configuración actualizada: Borradores y Futuros={build_drafts_and_future}")
        return jsonify({"status": "success", "build_drafts_and_future": build_drafts_and_future})

    # Check for GITHUB_TOKEN in env or .env file
    has_token = False
    if os.environ.get("GITHUB_TOKEN", "").strip():
        has_token = True
    else:
        for env_path in [".env", "../.env", "/app/.env"]:
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r") as f:
                        for line in f:
                            if line.strip().startswith("GITHUB_TOKEN="):
                                val = line.split("=", 1)[1].strip().replace('"', '').replace("'", "")
                                if val:
                                    has_token = True
                                break
                except:
                    pass
            if has_token:
                break

    return jsonify({
        "build_drafts_and_future": build_drafts_and_future, 
        "repo_url": REPO_URL,
        "github_token_configured": has_token
    })

@app.route("/api/status")
def status():
    update_activity()
    is_running = hugo_proc is not None and hugo_proc.poll() is None
    port_open = False
    
    # Also verify if the port 1313 is open
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 1313))
        port_open = True
    except:
        pass
    finally:
        s.close()
        
    return jsonify({
        "running": is_running or port_open,
        "pid": hugo_proc.pid if hugo_proc and hugo_proc.poll() is None else None,
        "metrics": build_metrics,
        "recent_files": get_recent_edited_files()
    })

@app.route("/api/start", methods=["POST"])
def start():
    update_activity()
    host = request.headers.get("Host", last_host_ip)
    ip_or_domain = host.split(":")[0] if ":" in host else host
    threading.Thread(target=run_clone_and_start, args=(ip_or_domain,), daemon=True).start()
    return jsonify({"status": "starting"})

@app.route("/api/restart", methods=["POST"])
def restart():
    update_activity()
    host = request.headers.get("Host", last_host_ip)
    ip_or_domain = host.split(":")[0] if ":" in host else host
    def do_restart():
        with proc_lock:
            stop_hugo_internal()
            time.sleep(1)
            start_hugo_internal(ip_or_domain)
    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({"status": "restarting"})

@app.route("/api/stop", methods=["POST"])
def stop():
    update_activity()
    with proc_lock:
        stop_hugo_internal()
    return jsonify({"status": "stopped"})

@app.route("/api/logs")
def get_logs():
    update_activity()
    return jsonify({"logs": LOGS_BUFFER})

def save_token_to_env(token):
    if not token:
        return
    env_path = ".env"
    lines = []
    found = False
    
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
            
            for i, line in enumerate(lines):
                if line.strip().startswith("GITHUB_TOKEN="):
                    lines[i] = f"GITHUB_TOKEN={token}\n"
                    found = True
                    break
        except Exception as e:
            add_log(f"⚠️ Error al leer .env para guardar token: {e}")
            
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"GITHUB_TOKEN={token}\n")
        
    try:
        with open(env_path, "w") as f:
            f.writelines(lines)
        add_log("💾 Token de GitHub guardado con éxito en el archivo .env.")
    except Exception as e:
        add_log(f"❌ Error al escribir token en .env: {e}")

@app.route("/api/push", methods=["POST"])
def git_push():
    update_activity()
    data = request.json or {}
    commit_msg = data.get("message", "").strip()
    token = data.get("token", "").strip()
    
    if token:
        save_token_to_env(token)
        os.environ["GITHUB_TOKEN"] = token
    
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        for env_path in [".env", "../.env", "/app/.env"]:
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r") as f:
                        for line in f:
                            if line.strip().startswith("GITHUB_TOKEN="):
                                token = line.split("=", 1)[1].strip().replace('"', '').replace("'", "")
                                break
                except:
                    pass
            if token:
                break
    
    if not commit_msg:
        commit_msg = f"Actualización desde Hugo Deck: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        
    if not os.path.exists(REPO_DIR):
        add_log("git-push: El repositorio no existe. Primero clónalo.")
        return jsonify({"status": "error", "message": "El repositorio no existe. Primero clónalo."}), 400
        
    try:
        # Configure local git user/email if not set, to avoid commits failing
        subprocess.run(["git", "config", "user.name", "Hugo Deck"], cwd=REPO_DIR)
        subprocess.run(["git", "config", "user.email", "hugo-deck@local"], cwd=REPO_DIR)
        
        # Check if there are changes to push
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
        if not status_res.stdout.strip():
            add_log("git-push: No hay cambios pendientes para subir.")
            return jsonify({"status": "warning", "message": "No hay cambios pendientes de subir."})
            
        add_log("git-push: Añadiendo archivos modificados/nuevos...")
        subprocess.run(["git", "add", "."], cwd=REPO_DIR)
        
        add_log(f"git-push: Creando commit con mensaje: '{commit_msg}'...")
        commit_res = subprocess.run(["git", "commit", "-m", commit_msg], cwd=REPO_DIR, capture_output=True, text=True)
        add_log(f"git-push: {commit_res.stdout.strip()}")
        
        add_log("git-push: Subiendo cambios a GitHub...")
        
        # Determine push URL
        push_url = "origin"
        if token:
            remote_res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO_DIR, capture_output=True, text=True)
            orig_url = remote_res.stdout.strip()
            if orig_url.startswith("https://"):
                clean_url = orig_url.replace("https://", "")
                if "@" in clean_url:
                    clean_url = clean_url.split("@", 1)[1]
                push_url = f"https://{token}@{clean_url}"
        
        # Get active branch name
        branch_res = subprocess.run(["git", "branch", "--show-current"], cwd=REPO_DIR, capture_output=True, text=True)
        branch = branch_res.stdout.strip() or "main"
        
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        push_res = subprocess.run(["git", "push", push_url, branch], cwd=REPO_DIR, capture_output=True, text=True, env=env)
        
        if push_res.returncode == 0:
            add_log("🎉 Git Push completado con éxito.")
            return jsonify({"status": "success", "message": "¡Cambios subidos a GitHub con éxito!"})
        else:
            err_msg = push_res.stderr.strip() or push_res.stdout.strip()
            add_log(f"❌ Error al hacer Git Push: {err_msg}")
            if "Authentication failed" in err_msg or "could not read Username" in err_msg:
                return jsonify({
                    "status": "error", 
                    "message": "Error de autenticación. Por favor, introduce un Token de Acceso Personal (PAT) válido de GitHub."
                }), 401
            return jsonify({"status": "error", "message": f"Error en Git Push: {err_msg}"}), 500
            
    except Exception as e:
        add_log(f"❌ Excepción durante Git Push: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    # Start background inactivity checker
    threading.Thread(target=inactivity_checker, daemon=True).start()
    
    # Automatically start Hugo on startup if repo exists
    if os.path.exists(os.path.join(REPO_DIR, "config.toml")) or os.path.exists(os.path.join(REPO_DIR, "hugo.toml")) or os.path.exists(os.path.join(REPO_DIR, "config.yaml")):
        add_log("Repo found. Starting Hugo server automatically...")
        start_hugo_internal()
    else:
        add_log("No active repository found. Please click 'Clone & Start' to clone and run.")
        
    app.run(host="0.0.0.0", port=1314)
