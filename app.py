import os
import subprocess
import shutil
import threading
import time
import socket
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

REPO_URL = os.environ.get("REPO_URL", "https://github.com/sherlockes/sherlockes.github.io.git")
REPO_DIR = "/site/repo"

HUGO_PREVIEW_URL = os.environ.get("HUGO_PREVIEW_URL", "").strip()
if not HUGO_PREVIEW_URL:
    for env_path in [".env", "../.env", "/site/.env", "/app/.env"]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        if line.strip().startswith("HUGO_PREVIEW_URL="):
                            val = line.split("=", 1)[1].strip().replace('"', '').replace("'", "")
                            if val:
                                HUGO_PREVIEW_URL = val
                            break
            except:
                pass
            if HUGO_PREVIEW_URL:
                break

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

@app.route("/api/delete-file", methods=["POST"])
def delete_file():
    update_activity()
    data = request.json or {}
    rel_file_path = data.get("file_path", "").strip()

    if not rel_file_path:
        return jsonify({"status": "error", "message": "No se proporcionó la ruta del archivo."}), 400

    full_path = os.path.abspath(os.path.join(REPO_DIR, rel_file_path))
    if not full_path.startswith(os.path.abspath(REPO_DIR)):
        return jsonify({"status": "error", "message": "Acceso denegado."}), 403

    if not os.path.exists(full_path):
        return jsonify({"status": "error", "message": "Archivo no encontrado."}), 404

    try:
        os.remove(full_path)
        add_log(f"🗑️ Artículo eliminado: {rel_file_path}")
        return jsonify({"status": "success", "message": "Artículo eliminado con éxito."})
    except Exception as e:
        add_log(f"❌ Error al eliminar artículo: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/move-file", methods=["POST"])
def move_file():
    update_activity()
    data = request.json or {}
    rel_file_path = data.get("file_path", "").strip()
    destination = data.get("destination", "").strip()

    if not rel_file_path or not destination:
        return jsonify({"status": "error", "message": "Faltan parámetros (file_path o destination)."}), 400

    full_src = os.path.abspath(os.path.join(REPO_DIR, rel_file_path))
    full_dst = os.path.abspath(os.path.join(REPO_DIR, destination))
    repo_abs = os.path.abspath(REPO_DIR)

    if not full_src.startswith(repo_abs) or not full_dst.startswith(repo_abs):
        return jsonify({"status": "error", "message": "Acceso denegado."}), 403

    if not os.path.exists(full_src):
        return jsonify({"status": "error", "message": "Archivo de origen no encontrado."}), 404

    if os.path.exists(full_dst):
        return jsonify({"status": "error", "message": "Ya existe un archivo en la ruta de destino."}), 409

    try:
        os.makedirs(os.path.dirname(full_dst), exist_ok=True)
        shutil.move(full_src, full_dst)
        new_rel_path = os.path.relpath(full_dst, REPO_DIR)
        add_log(f"📦 Artículo movido: {rel_file_path} → {new_rel_path}")
        return jsonify({"status": "success", "message": "Artículo movido con éxito.", "new_path": new_rel_path})
    except Exception as e:
        add_log(f"❌ Error al mover artículo: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/list-dirs", methods=["GET"])
def list_dirs():
    update_activity()
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return jsonify({"name": "content", "path": "content", "children": []})

    SKIP = {'.git', '.github', '__pycache__', 'node_modules', '.cache', '.hugo_build.lock'}

    def build_tree(abs_path, rel_path):
        children = []
        try:
            for entry in sorted(os.listdir(abs_path)):
                if entry.startswith('.') or entry in SKIP:
                    continue
                full = os.path.join(abs_path, entry)
                if os.path.isdir(full):
                    child_rel = f"{rel_path}/{entry}"
                    children.append({
                        "name": entry,
                        "path": child_rel,
                        "children": build_tree(full, child_rel)
                    })
        except PermissionError:
            pass
        return children

    return jsonify({
        "name": "content",
        "path": "content",
        "children": build_tree(content_dir, "content")
    })

@app.route("/api/list-articles", methods=["GET"])
def list_articles():
    update_activity()
    content_dir = os.path.join(REPO_DIR, "content")
    if not os.path.exists(content_dir):
        return jsonify([])

    import re as _re

    def slugify_simple(text):
        text = text.lower()
        accents = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
        for k, v in accents.items():
            text = text.replace(k, v)
        text = _re.sub(r'[^a-z0-9\s._-]', '', text)
        text = _re.sub(r'[\s_]+', '-', text)
        text = _re.sub(r'-+', '-', text)
        return text.strip('-')

    articles = []
    for root, _, files in os.walk(content_dir):
        for file in files:
            if not file.endswith(".md"):
                continue
            full_path = os.path.join(root, file)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    head = f.read(1200)

                title = file.rsplit(".", 1)[0]
                slug = ""
                url_path = ""

                fm = _re.search(r'^---\s*\n(.*?)\n---', head, _re.DOTALL)
                if fm:
                    for line in fm.group(1).split("\n"):
                        line = line.strip()
                        if line.startswith("title:"):
                            v = line.split(":", 1)[1].strip().strip('"').strip("'")
                            if v:
                                title = v
                        elif line.startswith("slug:"):
                            slug = line.split(":", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("url:"):
                            url_path = line.split(":", 1)[1].strip().strip('"').strip("'")

                if not url_path:
                    if slug:
                        url_path = f"/{slug}/"
                    else:
                        base = file.rsplit(".", 1)[0]
                        base = _re.sub(r'^\d{8}_', '', base)
                        base = _re.sub(r'^\d{4}-\d{2}-\d{2}-', '', base)
                        url_path = f"/{slugify_simple(base)}/"

                articles.append({
                    "title": title,
                    "file_path": os.path.relpath(full_path, REPO_DIR),
                    "url_path": url_path
                })
            except Exception:
                pass

    articles.sort(key=lambda x: x["title"].lower())
    return jsonify(articles)

@app.route("/api/upload-image", methods=["POST"])
def upload_image():
    update_activity()
    data = request.json or {}
    file_path = data.get("file_path", "").strip()
    image_b64 = data.get("image", "")
    is_clipboard = data.get("is_clipboard", False)
    is_thumbnail = data.get("is_thumbnail", False)
    overwrite_filename = data.get("overwrite_filename", "").strip()
    if isinstance(is_thumbnail, str):
        is_thumbnail = is_thumbnail.lower() in ("true", "1", "yes")
    add_log(f"📸 Procesando subida de imagen (is_thumbnail={is_thumbnail}, overwrite_filename={overwrite_filename})")
    
    if not file_path:
        return jsonify({"status": "error", "message": "Falta la ruta del artículo."}), 400
    if not image_b64:
        return jsonify({"status": "error", "message": "Falta la imagen."}), 400
        
    filename = os.path.basename(file_path)
    content = ""
    full_article_path = os.path.join(REPO_DIR, file_path)
    if os.path.exists(full_article_path):
        try:
            with open(full_article_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            add_log(f"⚠️ Error al leer el artículo para buscar fecha: {e}")
            
    # Parse date from front matter (creation: "YYYY-MM-DD" or date: "YYYY-MM-DD")
    date_str = ""
    slug = ""
    if content:
        import re
        creation_match = re.search(r'^creation:\s*["\']?(\d{4})[-_/]?(\d{2})[-_/]?(\d{2})["\']?', content, re.MULTILINE)
        if creation_match:
            date_str = f"{creation_match.group(1)}{creation_match.group(2)}{creation_match.group(3)}"
        else:
            date_match = re.search(r'^date:\s*["\']?(\d{4})[-_/]?(\d{2})[-_/]?(\d{2})["\']?', content, re.MULTILINE)
            if date_match:
                date_str = f"{date_match.group(1)}{date_match.group(2)}{date_match.group(3)}"

    # Fallback to filename parsing
    import re
    fn_match = re.match(r'^(\d{8})_(.*?)\.md$', filename)
    if fn_match:
        if not date_str:
            date_str = fn_match.group(1)
        slug = fn_match.group(2)
    else:
        if not date_str:
            import datetime
            date_str = datetime.date.today().strftime("%Y%m%d")
        slug = filename.rsplit(".", 1)[0]
        
    # Standardize slug: lowercase, replace hyphens with underscores
    slug = slug.lower().replace("-", "_")
    
    # Save directory: repo/static/images/
    images_dir = os.path.join(REPO_DIR, "static", "images")
    try:
        os.makedirs(images_dir, exist_ok=True)
    except Exception as e:
        add_log(f"❌ Error al crear directorio de imágenes: {e}")
        return jsonify({"status": "error", "message": "No se pudo crear el directorio de imágenes."}), 500
        
    if overwrite_filename:
        num_str = ""
        candidate_name = os.path.basename(overwrite_filename)
        candidate_path = os.path.join(images_dir, candidate_name)
    elif is_thumbnail:
        num_str = "00"
        candidate_name = f"{date_str}_{slug}_00.jpg"
        candidate_path = os.path.join(images_dir, candidate_name)
    else:
        # Find the first free number XX
        num = 1
        while True:
            num_str = f"{num:02d}"
            candidate_name = f"{date_str}_{slug}_{num_str}.jpg"
            candidate_path = os.path.join(images_dir, candidate_name)
            if not os.path.exists(candidate_path):
                break
            num += 1
        
    # Decode Base64 and write image
    import base64
    try:
        header, encoded = image_b64.split(",", 1) if "," in image_b64 else ("", image_b64)
        image_bytes = base64.b64decode(encoded)
        
        with open(candidate_path, "wb") as img_file:
            img_file.write(image_bytes)
            
        if overwrite_filename:
            add_log(f"📸 Sobreescrita imagen existente: {candidate_name} ({'Portapapeles' if is_clipboard else 'Archivo'})")
            return jsonify({
                "status": "success",
                "filename": candidate_name,
                "overwritten": True
            })
        else:
            add_log(f"📸 Guardada nueva imagen: {candidate_name} ({'Portapapeles' if is_clipboard else 'Archivo'})")
            return jsonify({
                "status": "success",
                "num": num_str,
                "filename": candidate_name
            })
    except Exception as e:
        add_log(f"❌ Error al guardar la imagen {candidate_name}: {e}")
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
    
    base_url = HUGO_PREVIEW_URL if HUGO_PREVIEW_URL else f"http://{host_ip}:1313"
    add_log(f"🚀 Launching Hugo Server (Borradores y Futuros: {'SÍ' if build_drafts_and_future else 'NO'}) on baseURL {base_url}...")
    try:
        cmd = [
            "hugo", "server",
            "--disableFastRender",
            "--bind", "0.0.0.0",
            "--baseURL", base_url,
            "--navigateToChanged",
            "--poll", "700ms"
        ]
        
        # Check if we are running behind a proxy with standard ports (80/443)
        import urllib.parse
        parsed_url = urllib.parse.urlparse(base_url)
        if parsed_url.scheme in ("http", "https"):
            netloc_parts = parsed_url.netloc.split(":")
            has_custom_port = len(netloc_parts) > 1 and netloc_parts[1] not in ("80", "443")
            if not has_custom_port and parsed_url.hostname not in ("localhost", "127.0.0.1", "0.0.0.0"):
                cmd.append("--appendPort=false")
                lr_port = "443" if parsed_url.scheme == "https" else "80"
                cmd.extend(["--liveReloadPort", lr_port])
                add_log(f"Proxy detected. Adding --appendPort=false and --liveReloadPort={lr_port}")

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

import io
import re
from PIL import Image

def slugify_simple(text):
    text = text.lower()
    accents = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
    for k, v in accents.items():
        text = text.replace(k, v)
    text = re.sub(r'[^a-z0-9\s.-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')

def parse_article_meta(file_path):
    title = ""
    tags = []
    date_str = ""
    slug = ""
    
    if not os.path.exists(file_path):
        return None
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Extract title
        title_match = re.search(r'^title:\s*["\']?([^"\n\']+)["\']?', content, re.MULTILINE | re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            
        # Extract slug
        slug_match = re.search(r'^slug:\s*["\']?([^"\n\']+)["\']?', content, re.MULTILINE | re.IGNORECASE)
        if slug_match:
            slug = slug_match.group(1).strip()
        else:
            base_name = os.path.basename(file_path).rsplit(".", 1)[0]
            clean_base = re.sub(r'^\d{8}_', '', base_name)
            clean_base = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', clean_base)
            slug = clean_base
            
        # Extract date
        date_match = re.search(r'^date:\s*["\']?([^"\n\'T\s]+)', content, re.MULTILINE | re.IGNORECASE)
        if date_match:
            date_str = date_match.group(1).replace("-", "").strip()
        else:
            fn = os.path.basename(file_path)
            fn_date_match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', fn)
            if fn_date_match:
                date_str = "".join(fn_date_match.groups())
            else:
                import datetime
                date_str = datetime.date.today().strftime("%Y%m%d")
                
        # Extract tags
        tags_section_match = re.search(r'^tags:\s*\n((?:\s*-\s*.*?\n)+)', content, re.MULTILINE | re.IGNORECASE)
        if tags_section_match:
            tags_lines = tags_section_match.group(1).split("\n")
            for line in tags_lines:
                tag_match = re.search(r'-\s*["\']?(.*?)["\']?$', line)
                if tag_match:
                    tags.append(tag_match.group(1).strip())
        else:
            tags_inline_match = re.search(r'^tags:\s*\[(.*?)\]', content, re.MULTILINE | re.IGNORECASE)
            if tags_inline_match:
                tags = [t.strip().strip('"').strip("'") for t in tags_inline_match.group(1).split(",")]
                
    except Exception as e:
        print(f"Error parsing article meta: {e}")
        
    return {
        "title": title,
        "tags": tags,
        "date_str": date_str,
        "slug": slug
    }

@app.route("/api/generate-thumbnail", methods=["POST"])
def generate_thumbnail():
    data = request.json or {}
    file_path = data.get("file_path")
    custom_title = data.get("custom_title", "").strip()
    
    if not file_path:
        return jsonify({"status": "error", "message": "Falta la ruta del archivo."}), 400
        
    full_path = os.path.abspath(os.path.join(REPO_DIR, file_path))
    meta = parse_article_meta(full_path)
    if not meta:
        return jsonify({"status": "error", "message": f"No se pudo leer la metadata del artículo en la ruta: {file_path}"}), 400
        
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        dotenv_path = "/site/.env"
        if os.path.exists(dotenv_path):
            with open(dotenv_path, "r") as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                        
    if not api_key:
        return jsonify({
            "status": "error", 
            "message": "Falta la clave API de OpenRouter. Por favor, añádela como OPENROUTER_API_KEY en tu archivo .env y reinicia el servicio."
        }), 400
        
    model = os.environ.get("OPENROUTER_IMAGE_MODEL")
    if not model:
        dotenv_path = "/site/.env"
        if os.path.exists(dotenv_path):
            with open(dotenv_path, "r") as f:
                for line in f:
                    if line.startswith("OPENROUTER_IMAGE_MODEL="):
                        model = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    if not model:
        model = "google/gemini-2.5-flash-image"
        
    image_title = custom_title if custom_title else meta["title"]
    
    # Read the article content snippet
    article_content = ""
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            article_content = f.read()
    except Exception as e:
        add_log(f"⚠️ Error al leer contenido completo para prompt: {e}")
        
    # Generate optimized prompt via Gemini 2.5 Flash on OpenRouter
    add_log("🧠 Optimizando prompt para el thumbnail con Gemini...")
    optimized_prompt = ""
    import urllib.request
    import json
    try:
        llm_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        llm_payload = {
            "model": "google/gemini-2.5-flash",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert graphic designer. Read the article title, tags, and content, "
                        "and output a single detailed prompt for an image generation model (FLUX/SDXL) to design the article's cover/thumbnail.\n"
                        "Requirements for the generated image:\n"
                        f"1. The text '{image_title}' must be written clearly and legibly at the top of the image in a modern, elegant, clean sans-serif font. The text must occupy the full width and approximately the top 90px of the 300px image, using a large font size to make it highly visible and prominent.\n"
                        f"2. Below the title, include high-quality, professional, minimalist 3D illustrations, drawings, or icons representing the core topic of the article and specifically referring to its tags: {', '.join(meta['tags'])}.\n"
                        "3. The background must be solid, pure white. Do not use dark, black, or colorful backgrounds. The entire background of the image must be completely white.\n"
                        "4. The illustrations and drawings must occupy all the remaining useful space of the 300x300px canvas, except for the top 90px (reserved for the title), the bottom 50px (reserved for the blog name, from y=250px to y=300px), and the bottom-right 100x100px corner (reserved for the logo, from x=200px to x=300px and y=200px to y=300px) which must be kept completely clean and empty of main subjects.\n"
                        "Important: Return ONLY the raw prompt text. No introduction, no quotes, no markdown blocks."
                    )
                },
                {
                    "role": "user",
                    "content": f"Title: {image_title}\nTags: {', '.join(meta['tags'])}\nContent snippet:\n{article_content[:2000]}"
                }
            ],
            "temperature": 0.5
        }
        
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(llm_payload).encode("utf-8"),
            headers=llm_headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            choices = res_data.get("choices", [])
            if choices:
                optimized_prompt = choices[0].get("message", {}).get("content", "").strip()
                if optimized_prompt.startswith('"') and optimized_prompt.endswith('"'):
                    optimized_prompt = optimized_prompt[1:-1].strip()
                add_log(f"🧠 Prompt optimizado por IA: {optimized_prompt}")
    except Exception as e:
        add_log(f"⚠️ Error al llamar a Gemini para optimizar el prompt: {e}")
        
    if not optimized_prompt:
        # Fallback if LLM fails
        tags_str = ", ".join(meta["tags"]) if meta["tags"] else "tecnología"
        optimized_prompt = f"A beautiful, clean, modern tech illustration representing the theme: {tags_str}. The text '{image_title}' must be written clearly at the top in a clean, legible, modern font, spanning the full width of the image and occupying approximately the top 90px with a large font size. Solid pure white background. Minimalist 3D render, centered composition. The illustrations must occupy the remaining space, keeping the bottom 50px and bottom-right 100x100px corner clean."

    payload = {
        "model": model,
        "prompt": optimized_prompt,
        "aspect_ratio": "1:1",
        "n": 1
    }
    
    add_log(f"🎨 Llamando a OpenRouter ({model}) para generar thumbnail...")
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/images",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            
        img_data = res_data.get("data", [])
        if not img_data:
            add_log(f"❌ Respuesta API de OpenRouter vacía: {res_data}")
            return jsonify({"status": "error", "message": "No se recibieron datos de imagen en la respuesta de OpenRouter."}), 500
            
        first_img = img_data[0]
        img_url = first_img.get("url")
        img_b64 = first_img.get("b64_json")
        
        img_bytes = None
        if img_b64:
            import base64
            img_bytes = base64.b64decode(img_b64)
        elif img_url:
            with urllib.request.urlopen(img_url, timeout=30) as img_res:
                img_bytes = img_res.read()
                
        if not img_bytes:
            return jsonify({"status": "error", "message": "No se pudo descargar o decodificar la imagen generada."}), 500
            
        gen_img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        gen_img = gen_img.resize((300, 300), Image.Resampling.LANCZOS)

        overlay_path = "/site/thumbnail_overlay.png"
        if not os.path.exists(overlay_path):
            overlay_path = os.path.join(REPO_DIR, "thumbnail_overlay.png")
            
        if os.path.exists(overlay_path):
            try:
                overlay_img = Image.open(overlay_path).convert("RGBA")
                if overlay_img.size != (300, 300):
                    overlay_img = overlay_img.resize((300, 300), Image.Resampling.LANCZOS)
                
                # If the overlay is fully opaque, key out the white background
                alphas = [p[3] for p in overlay_img.getdata()]
                if not alphas or min(alphas) == 255:
                    datas = overlay_img.getdata()
                    newData = []
                    for item in datas:
                        # If it is white or close to white, make it transparent
                        if item[0] > 250 and item[1] > 250 and item[2] > 250:
                            newData.append((255, 255, 255, 0))
                        else:
                            newData.append(item)
                    overlay_img.putdata(newData)
                    add_log("📸 Se detectó plantilla opaca; se convirtió el fondo blanco a transparente.")
                
                gen_img = Image.alpha_composite(gen_img, overlay_img)
                add_log("📸 Se aplicó la plantilla superpuesta (thumbnail_overlay.png)")
            except Exception as ov_err:
                add_log(f"⚠️ Error al aplicar superposición de plantilla: {ov_err}")
        else:
            add_log("⚠️ No se encontró la plantilla 'thumbnail_overlay.png' en la raíz. Generando imagen sin plantilla.")
                 
        target_dir = os.path.join(REPO_DIR, "static", "images")
        os.makedirs(target_dir, exist_ok=True)
        
        # Try to find the exact filename from the article's front matter
        thumbnail_match = re.search(r'^thumbnail:\s*["\']?(?:images/)?([^"\n\']+)["\']?', article_content, re.MULTILINE | re.IGNORECASE)
        if thumbnail_match:
            filename = thumbnail_match.group(1).strip()
        else:
            # Fallback: keep underscores of the slug
            base_name = os.path.basename(file_path).rsplit(".", 1)[0]
            clean_base = re.sub(r'^\d{8}_', '', base_name)
            clean_base = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', clean_base)
            filename = f"{meta['date_str']}_{clean_base}_00.jpg"
            
        target_path = os.path.join(target_dir, filename)
        
        rgb_img = gen_img.convert("RGB")
        rgb_img.save(target_path, "JPEG", quality=90)
        
        add_log(f"🎉 Portada thumbnail generada y guardada con éxito en: static/images/{filename}")
        
        return jsonify({
            "status": "success",
            "filename": filename,
            "web_url": f"/static/images/{filename}"
        })
        
    except Exception as e:
        add_log(f"❌ Error al generar thumbnail: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/static/images/<path:filename>")
def serve_repo_static_images(filename):
    return send_from_directory(os.path.join(REPO_DIR, "static", "images"), filename)

@app.route("/images/<path:filename>")
def serve_repo_images(filename):
    return send_from_directory(os.path.join(REPO_DIR, "static", "images"), filename)

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
        "github_token_configured": has_token,
        "hugo_preview_url": HUGO_PREVIEW_URL
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
            if any(phrase in err_msg for phrase in ["Authentication failed", "could not read Username", "could not read Password", "terminal prompts disabled"]):
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
