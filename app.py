from flask import Flask, render_template, request, send_file, jsonify
import os
import subprocess
import shutil
from werkzeug.utils import secure_filename
from datetime import datetime
import zipfile
import tempfile

app = Flask(__name__)

# إعدادات التطبيق
app.config['MAX_CONTENT_LENGTH'] = 1000 * 1024 * 1024  # 1GB
app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
app.config['OUTPUT_FOLDER'] = '/tmp/outputs'

# إنشاء المجلدات
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv', 'm4v'}

def allowed_file(filename):
    """التحقق من امتداد الملف"""
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
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        return float(result.stdout.strip())
    except subprocess.TimeoutExpired:
        print("FFprobe timeout")
        return 0
    except Exception as e:
        print(f"Error getting duration: {e}")
        return 0

@app.route('/')
def index():
    """الصفحة الرئيسية"""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """رفع الفيديو والحصول على معلوماته"""
    try:
        print("📥 Upload request received")
        
        if 'video' not in request.files:
            print("❌ No video in request")
            return jsonify({'error': 'لا يوجد ملف'}), 400
        
        file = request.files['video']
        
        if file.filename == '':
            print("❌ Empty filename")
            return jsonify({'error': 'لم تختر ملف'}), 400
        
        print(f"📁 File: {file.filename}")
        
        if file and allowed_file(file.filename):
            # تأمين اسم الملف
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # حفظ الملف بـ chunks للملفات الكبيرة
            print(f"💾 Saving file to: {filepath}")
            chunk_size = 1024 * 1024  # 1MB chunks
            
            with open(filepath, 'wb') as f:
                while True:
                    chunk = file.stream.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
            
            file_size = os.path.getsize(filepath)
            print(f"✅ File saved: {file_size} bytes")
            
            # الحصول على المدة
            duration = get_video_duration(filepath)
            print(f"📊 Duration: {duration}s, Size: {file_size} bytes")
            
            return jsonify({
                'success': True,
                'filename': filename,
                'duration': duration,
                'size': file_size
            })
        
        return jsonify({'error': 'نوع الملف غير مدعوم'}), 400
    
    except Exception as e:
        print(f"❌ Upload error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'خطأ في رفع الملف: {str(e)}'}), 500

@app.route('/split', methods=['POST'])
def split_video():
    """تقسيم الفيديو باستخدام FFmpeg"""
    try:
        data = request.get_json()
        filename = data.get('filename')
        clip_duration = int(data.get('duration', 30))
        quality = data.get('quality', 'high')
        
        print(f"✂️ Splitting: {filename}, clip_duration: {clip_duration}s, quality: {quality}")
        
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(input_path):
            print(f"❌ File not found: {input_path}")
            return jsonify({'error': 'الملف غير موجود'}), 404
        
        # إنشاء مجلد للمخرجات
        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
        os.makedirs(output_dir, exist_ok=True)
        
        # تحديد جودة الإخراج
        if quality == 'high':
            codec_params = ['-c', 'copy']  # Stream copy - أسرع وبدون فقدان جودة
        elif quality == 'medium':
            codec_params = ['-c:v', 'libx264', '-crf', '23', '-preset', 'fast', '-c:a', 'aac', '-b:a', '128k']
        else:  # low/whatsapp
            codec_params = ['-vf', 'scale=-2:720', '-c:v', 'libx264', '-crf', '28', '-preset', 'fast', '-c:a', 'aac', '-b:a', '96k']
        
        # أمر FFmpeg للتقسيم
        output_pattern = os.path.join(output_dir, 'part_%03d.mp4')
        
        cmd = [
            'ffmpeg',
            '-i', input_path,
            *codec_params,
            '-map', '0',
            '-segment_time', str(clip_duration),
            '-f', 'segment',
            '-reset_timestamps', '1',
            '-avoid_negative_ts', 'make_zero',
            output_pattern
        ]
        
        print(f"🎬 Running FFmpeg command...")
        result = subprocess.run(
            cmd, 
            check=True, 
            capture_output=True, 
            timeout=600,
            text=True
        )
        
        print(f"✅ FFmpeg completed")
        
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
        
        print(f"✅ Split successful: {len(clips)} clips created")
        
        return jsonify({
            'success': True,
            'clips': clips_info,
            'output_dir': filename.rsplit('.', 1)[0]
        })
    
    except subprocess.TimeoutExpired:
        print("❌ FFmpeg timeout")
        return jsonify({'error': 'انتهت مهلة التقسيم. جرب فيديو أقصر'}), 500
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg error: {e.stderr}")
        return jsonify({'error': f'فشل التقسيم: {e.stderr}'}), 500
    except Exception as e:
        print(f"❌ Split error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'خطأ في التقسيم: {str(e)}'}), 500

@app.route('/download/<path:filepath>')
def download_file(filepath):
    """تحميل مقطع واحد"""
    try:
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filepath)
        
        if not os.path.exists(file_path):
            return jsonify({'error': 'الملف غير موجود'}), 404
        
        print(f"📥 Downloading: {file_path}")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=os.path.basename(file_path),
            mimetype='video/mp4'
        )
    except Exception as e:
        print(f"❌ Download error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/download-zip/<output_dir>')
def download_zip(output_dir):
    """تحميل جميع المقاطع كـ ZIP"""
    try:
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_dir)
        
        if not os.path.exists(output_path):
            return jsonify({'error': 'المجلد غير موجود'}), 404
        
        print(f"📦 Creating ZIP for: {output_dir}")
        
        # إنشاء ZIP في temp folder
        temp_dir = tempfile.gettempdir()
        zip_filename = f"{output_dir}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        # إنشاء ZIP
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(output_path):
                for file in files:
                    if file.endswith('.mp4'):
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, file)
                        print(f"  ✅ Added to ZIP: {file}")
        
        print(f"✅ ZIP created: {zip_path}")
        
        # تحميل ZIP
        response = send_file(
            zip_path,
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )
        
        response.headers['Content-Length'] = os.path.getsize(zip_path)
        response.headers['Cache-Control'] = 'no-cache'
        
        # حذف ZIP بعد التحميل
        @response.call_on_close
        def cleanup():
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    print(f"🗑️ Cleaned up ZIP: {zip_path}")
            except:
                pass
        
        return response
        
    except Exception as e:
        print(f"❌ ZIP Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'فشل إنشاء ZIP: {str(e)}'}), 500

@app.route('/cleanup/<filename>')
def cleanup(filename):
    """حذف الملفات المؤقتة"""
    try:
        # حذف الملف الأصلي
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(input_path):
            os.remove(input_path)
            print(f"🗑️ Deleted: {input_path}")
        
        # حذف مجلد المخرجات
        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
            print(f"🗑️ Deleted folder: {output_dir}")
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Cleanup error: {e}")
        return jsonify({'error': str(e)}), 500

# Error handlers
@app.errorhandler(413)
def request_entity_too_large(error):
    """ملف كبير جداً"""
    return jsonify({'error': 'الملف كبير جداً. الحد الأقصى 1GB'}), 413

@app.errorhandler(500)
def internal_error(error):
    """خطأ في السيرفر"""
    print(f"❌ Internal error: {error}")
    return jsonify({'error': 'خطأ في السيرفر'}), 500

@app.errorhandler(404)
def not_found(error):
    """صفحة غير موجودة"""
    return jsonify({'error': 'الصفحة غير موجودة'}), 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"🚀 Starting server on port {port}")
    app.run(
        debug=False,
        host='0.0.0.0',
        port=port,
        threaded=True
    )
