from flask import Flask, render_template, request, send_file, jsonify, send_from_directory
import os
import subprocess
import shutil
from werkzeug.utils import secure_filename
from datetime import datetime
import zipfile
import tempfile

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'

# إنشاء المجلدات
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_video_duration(video_path):
    """الحصول على مدة الفيديو باستخدام FFprobe"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration: {e}")
        return 0

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """رفع الفيديو والحصول على معلوماته"""
    if 'video' not in request.files:
        return jsonify({'error': 'لا يوجد ملف'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'لم تختر ملف'}), 400
    
    if file and allowed_file(file.filename):
        # حفظ الملف
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # الحصول على المدة
        duration = get_video_duration(filepath)
        file_size = os.path.getsize(filepath)
        
        return jsonify({
            'success': True,
            'filename': filename,
            'duration': duration,
            'size': file_size
        })
    
    return jsonify({'error': 'نوع الملف غير مدعوم'}), 400

@app.route('/split', methods=['POST'])
def split_video():
    """تقسيم الفيديو باستخدام FFmpeg"""
    data = request.get_json()
    filename = data.get('filename')
    clip_duration = int(data.get('duration', 30))
    quality = data.get('quality', 'high')
    
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(input_path):
        return jsonify({'error': 'الملف غير موجود'}), 404
    
    # إنشاء مجلد للمخرجات
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
    os.makedirs(output_dir, exist_ok=True)
    
    # تحديد جودة الإخراج
    if quality == 'high':
        codec_params = ['-c', 'copy']  # Stream copy
    elif quality == 'medium':
        codec_params = ['-c:v', 'libx264', '-crf', '23', '-preset', 'fast', '-c:a', 'aac']
    else:  # low/whatsapp
        codec_params = ['-vf', 'scale=-2:720', '-c:v', 'libx264', '-crf', '28', '-preset', 'fast', '-c:a', 'aac']
    
    # أمر FFmpeg للتقسيم
    output_pattern = os.path.join(output_dir, 'part_%03d.mp4')
    
    cmd = [
        'ffmpeg', '-i', input_path,
        *codec_params,
        '-map', '0',
        '-segment_time', str(clip_duration),
        '-f', 'segment',
        '-reset_timestamps', '1',
        output_pattern
    ]
    
    try:
        # تنفيذ الأمر
        subprocess.run(cmd, check=True, capture_output=True)
        
        # جمع الملفات الناتجة
        clips = sorted([f for f in os.listdir(output_dir) if f.endswith('.mp4')])
        clips_info = []
        
        for clip in clips:
            clip_path = os.path.join(output_dir, clip)
            clips_info.append({
                'name': clip,
                'size': os.path.getsize(clip_path),
                'path': os.path.join(filename.rsplit('.', 1)[0], clip)
            })
        
        return jsonify({
            'success': True,
            'clips': clips_info,
            'output_dir': filename.rsplit('.', 1)[0]
        })
    
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'فشل التقسيم: {str(e)}'}), 500

@app.route('/download/<path:filepath>')
def download_file(filepath):
    """تحميل مقطع واحد"""
    try:
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filepath)
        if os.path.exists(file_path):
            # ✅ تحسين التحميل لـ Cloudflare
            return send_file(
                file_path, 
                as_attachment=True,
                download_name=os.path.basename(file_path),
                mimetype='video/mp4'
            )
        return jsonify({'error': 'الملف غير موجود'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download-zip/<output_dir>')
def download_zip(output_dir):
    """تحميل جميع المقاطع كـ ZIP"""
    try:
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_dir)
        
        if not os.path.exists(output_path):
            return jsonify({'error': 'المجلد غير موجود'}), 404
        
        # ✅ إنشاء ZIP في temp folder بدلاً من outputs
        temp_dir = tempfile.gettempdir()
        zip_filename = f"{output_dir}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        # إنشاء ZIP
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_path):
                for file in files:
                    if file.endswith('.mp4'):
                        file_path = os.path.join(root, file)
                        # إضافة الملف مع اسم بسيط
                        zipf.write(file_path, file)
        
        # ✅ تحميل مع headers صحيحة لـ Cloudflare
        response = send_file(
            zip_path,
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )
        
        # ✅ إضافة headers لضمان التحميل
        response.headers['Content-Length'] = os.path.getsize(zip_path)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        # حذف الـ ZIP بعد إرساله
        @response.call_on_close
        def cleanup():
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except:
                pass
        
        return response
        
    except Exception as e:
        print(f"ZIP Error: {e}")
        return jsonify({'error': f'فشل إنشاء ZIP: {str(e)}'}), 500

@app.route('/cleanup/<filename>')
def cleanup(filename):
    """حذف الملفات المؤقتة"""
    try:
        # حذف الملف الأصلي
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(input_path):
            os.remove(input_path)
        
        # حذف مجلد المخرجات
        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ Error handlers لـ Cloudflare
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'الملف كبير جداً. الحد الأقصى 500 ميجا'}), 413

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'خطأ في السيرفر'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # ✅ غير من 5000 إلى 8080
    app.run(
        debug=False,
        host='0.0.0.0',
        port=port,
        threaded=True
    )
