#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网实时协作Markdown文档系统
基于Flask和Flask-SocketIO构建
教师可通过上传Excel文件进行学生分组
学生通过姓名登录后自动进入对应小组
同组成员可实时同步编辑同一份Markdown文档
"""

import os
import json
import pandas as pd
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, join_room, leave_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'lan-share-document-secret-key-2023'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB文件大小限制
app.config['UPLOAD_FOLDER'] = 'uploads'

# 确保上传目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 数据存储路径
GROUPS_FILE = 'groups.json'
DOCUMENTS_FILE = 'documents.json'

# 内存中的数据存储
groups_data = {}  # 分组数据
documents_data = {}  # 文档数据（内存缓存）
active_class = None  # 当前激活的班级
teacher_password = 'teacher123'  # 教师密码（建议在生产环境中修改）

# DeepSeek API配置（存储在内存中，服务器重启后重置）
deepseek_api_key = ''  # API密钥
deepseek_api_url = 'https://api.deepseek.com/v1/chat/completions'  # API地址
deepseek_model = 'deepseek-chat'  # 默认模型

# 加载已有数据
def load_data():
    global groups_data, documents_data
    try:
        if os.path.exists(GROUPS_FILE):
            with open(GROUPS_FILE, 'r', encoding='utf-8') as f:
                groups_data = json.load(f)
    except Exception as e:
        print(f"加载分组数据失败: {e}")
        groups_data = {}
    
    try:
        if os.path.exists(DOCUMENTS_FILE):
            with open(DOCUMENTS_FILE, 'r', encoding='utf-8') as f:
                documents_data = json.load(f)
    except Exception as e:
        print(f"加载文档数据失败: {e}")
        documents_data = {}

def save_groups():
    """保存分组数据到文件"""
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存分组数据失败: {e}")
        return False

def save_documents():
    """保存文档数据到文件（可选）"""
    try:
        with open(DOCUMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(documents_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存文档数据失败: {e}")
        return False

# DeepSeek API配置（从前端获取，不保存到文件）
# 以下函数已移除，因为API配置现在从前端获取

# DeepSeek API调用函数
def call_deepseek_api(api_key, api_url, model, prompt, max_tokens=1000, temperature=0.7, max_retries=3):
    """调用DeepSeek API，支持重试机制（使用OpenAI客户端库）"""
    if not api_key:
        return None, "API密钥未配置"
    
    # 检查API密钥格式
    if not api_key.startswith('sk-'):
        return None, "API密钥格式不正确，应以'sk-'开头"
    
    # 重试机制
    for attempt in range(max_retries):
        try:
            print(f"尝试第 {attempt + 1} 次调用DeepSeek API...")
            print(f"API密钥前8位: {api_key[:8]}...")
            print(f"API地址: {api_url}")
            print(f"模型: {model}")
            
            # 导入OpenAI客户端
            from openai import OpenAI
            
            # 创建OpenAI客户端
            client = OpenAI(
                api_key=api_key,
                base_url=api_url.replace('/v1/chat/completions', '') if api_url else "https://api.deepseek.com"
            )
            
            # 调用API
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False
            )
            
            content = response.choices[0].message.content
            print(f"第 {attempt + 1} 次尝试成功，生成内容长度: {len(content)}")
            return content, None
            
        except Exception as e:
            error_msg = f"API调用失败: {str(e)}"
            print(f"第 {attempt + 1} 次尝试失败: {error_msg}")
            
            # 如果是最后一次尝试，返回更详细的错误信息
            if attempt == max_retries - 1:
                # 尝试从异常中提取更多信息
                error_detail = str(e)
                if hasattr(e, 'response') and e.response:
                    try:
                        error_detail = e.response.text
                    except:
                        pass
                return None, f"API调用失败: Error code: {getattr(e, 'status_code', 'N/A')} - {error_detail}"
            
            # 等待后重试
            print(f"等待 {attempt + 1} 秒后重试...")
            import time
            time.sleep(attempt + 1)
            continue
    
    return None, "所有重试尝试都失败了"

# 初始化加载数据
load_data()

# ==================== 教师端路由 ====================

@app.route('/')
def index():
    """首页重定向"""
    return redirect(url_for('student_login'))

@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    """教师登录"""
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        if password == teacher_password:
            session['teacher_logged_in'] = True
            return redirect(url_for('teacher_dashboard'))
        else:
            return render_template('teacher_login.html', error='密码错误')
    
    return render_template('teacher_login.html')

@app.route('/teacher/logout')
def teacher_logout():
    """教师登出"""
    session.pop('teacher_logged_in', None)
    return redirect(url_for('teacher_login'))

@app.route('/teacher/dashboard')
def teacher_dashboard():
    """教师控制台"""
    if not session.get('teacher_logged_in'):
        return redirect(url_for('teacher_login'))
    
    return render_template('teacher_dashboard.html', 
                          groups_data=groups_data,
                          active_class=active_class)

@app.route('/teacher/upload', methods=['POST'])
def upload_excel():
    """上传Excel文件并解析分组"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    if 'excel_file' not in request.files:
        return jsonify({'success': False, 'message': '未选择文件'}), 400
    
    file = request.files['excel_file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '未选择文件'}), 400
    
    # 检查文件扩展名
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        return jsonify({'success': False, 'message': '只支持.xlsx和.xls格式'}), 400
    
    try:
        # 保存文件
        filename = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # 读取Excel文件
        if file.filename.endswith('.xlsx'):
            df = pd.read_excel(filepath, engine='openpyxl')
        else:
            df = pd.read_excel(filepath, engine='xlrd')
        
        # 检查列名
        required_columns = ['班级', '组别', '姓名', '组长']
        if not all(col in df.columns for col in required_columns):
            return jsonify({'success': False, 'message': 'Excel文件必须包含"班级"、"组别"、"姓名"、"组长"四列'}), 400
        
        # 解析分组数据
        new_groups = {}
        for _, row in df.iterrows():
            class_name = str(row['班级']).strip()
            group_name = str(row['组别']).strip()
            student_name = str(row['姓名']).strip()
            leader_flag = str(row['组长']).strip()
            
            if class_name not in new_groups:
                new_groups[class_name] = {}
            
            if group_name not in new_groups[class_name]:
                new_groups[class_name][group_name] = {
                    'students': [],
                    'leader': None
                }
            
            # 添加学生到组
            if student_name not in new_groups[class_name][group_name]['students']:
                new_groups[class_name][group_name]['students'].append(student_name)
            
            # 检查是否为组长
            if leader_flag in ['是', 'yes', 'Yes', 'YES', '1', 'true', 'True', 'TRUE']:
                # 设置组长
                new_groups[class_name][group_name]['leader'] = student_name
        
        # 更新全局分组数据
        global groups_data
        groups_data.update(new_groups)
        
        # 保存到文件
        if save_groups():
            return jsonify({
                'success': True, 
                'message': f'成功上传并解析了{len(new_groups)}个班级的分组数据',
                'classes': list(new_groups.keys())
            })
        else:
            return jsonify({'success': False, 'message': '解析成功但保存失败'}), 500
            
    except Exception as e:
        print(f"解析Excel文件失败: {e}")
        return jsonify({'success': False, 'message': f'解析文件失败: {str(e)}'}), 500

@app.route('/teacher/set_active_class', methods=['POST'])
def set_active_class():
    """设置当前激活的班级"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    class_name = request.form.get('class_name', '').strip()
    if not class_name:
        return jsonify({'success': False, 'message': '班级名称不能为空'}), 400
    
    global active_class
    active_class = class_name
    
    # 通知所有学生班级已切换
    socketio.emit('class_changed', {'class_name': class_name})
    
    return jsonify({'success': True, 'message': f'已激活班级: {class_name}'})

@app.route('/teacher/get_groups')
def get_groups():
    """获取分组数据"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    return jsonify({
        'success': True,
        'groups_data': groups_data,
        'active_class': active_class
    })

@app.route('/teacher/reset_document', methods=['POST'])
def reset_document():
    """重置指定小组的文档内容"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    class_name = request.form.get('class_name', '').strip()
    group_name = request.form.get('group_name', '').strip()
    
    if not class_name or not group_name:
        return jsonify({'success': False, 'message': '班级和组别不能为空'}), 400
    
    # 清空文档内容
    room_id = f"group_{class_name}_{group_name}"
    if room_id in documents_data:
        documents_data[room_id] = ""
        save_documents()
    
    # 通知该小组所有成员
    socketio.emit('document_reset', {'message': '教师已重置文档内容'}, room=room_id)
    
    return jsonify({'success': True, 'message': f'已重置{class_name}-{group_name}的文档内容'})

@app.route('/teacher/delete_class', methods=['POST'])
def delete_class():
    """删除班级"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    class_name = request.form.get('class_name', '').strip()
    
    if not class_name:
        return jsonify({'success': False, 'message': '班级名称不能为空'}), 400
    
    global groups_data, active_class
    
    # 检查班级是否存在
    if class_name not in groups_data:
        return jsonify({'success': False, 'message': f'班级"{class_name}"不存在'}), 404
    
    # 如果删除的是当前激活的班级，需要清除激活状态
    if active_class == class_name:
        active_class = None
        # 通知所有学生班级已删除
        socketio.emit('class_deleted', {'class_name': class_name})
    
    # 删除班级数据
    del groups_data[class_name]
    
    # 删除相关的文档数据
    for group_name in list(documents_data.keys()):
        if group_name.startswith(f"group_{class_name}_"):
            del documents_data[group_name]
    
    # 保存数据
    if save_groups():
        save_documents()
        return jsonify({'success': True, 'message': f'已成功删除班级"{class_name}"'})
    else:
        return jsonify({'success': False, 'message': '删除班级成功但保存数据失败'}), 500

@app.route('/teacher/delete_student', methods=['POST'])
def delete_student():
    """删除学生"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    class_name = request.form.get('class_name', '').strip()
    group_name = request.form.get('group_name', '').strip()
    student_name = request.form.get('student_name', '').strip()
    
    if not class_name or not group_name or not student_name:
        return jsonify({'success': False, 'message': '班级、组别和学生姓名不能为空'}), 400
    
    # 检查班级是否存在
    if class_name not in groups_data:
        return jsonify({'success': False, 'message': f'班级"{class_name}"不存在'}), 404
    
    # 检查组是否存在
    if group_name not in groups_data[class_name]:
        return jsonify({'success': False, 'message': f'组别"{group_name}"不存在'}), 404
    
    # 检查学生是否存在
    if student_name not in groups_data[class_name][group_name]['students']:
        return jsonify({'success': False, 'message': f'学生"{student_name}"不存在'}), 404
    
    # 如果是组长，需要重新指定组长或清除组长
    if groups_data[class_name][group_name].get('leader') == student_name:
        groups_data[class_name][group_name]['leader'] = None
    
    # 删除学生
    groups_data[class_name][group_name]['students'].remove(student_name)
    
    # 如果小组为空，删除该小组
    if not groups_data[class_name][group_name]['students']:
        del groups_data[class_name][group_name]
        
        # 如果班级为空，删除该班级
        if not groups_data[class_name]:
            del groups_data[class_name]
            global active_class
            if active_class == class_name:
                active_class = None
                socketio.emit('class_deleted', {'class_name': class_name})
    
    # 保存数据
    if save_groups():
        # 通知该学生已从小组中移除
        room_id = f"group_{class_name}_{group_name}"
        socketio.emit('student_removed', {
            'student_name': student_name,
            'message': f'学生{student_name}已被教师从小组中移除'
        }, room=room_id)
        
        return jsonify({'success': True, 'message': f'已成功删除学生"{student_name}"'})
    else:
        return jsonify({'success': False, 'message': '删除学生成功但保存数据失败'}), 500

# ==================== DeepSeek API路由 ====================

@app.route('/teacher/deepseek_config', methods=['GET', 'POST'])
def deepseek_config():
    """DeepSeek API配置页面（配置仅用于显示，实际使用从前端获取）"""
    if not session.get('teacher_logged_in'):
        return redirect(url_for('teacher_login'))
    
    if request.method == 'POST':
        global deepseek_api_key, deepseek_api_url, deepseek_model
        
        api_key = request.form.get('api_key', '').strip()
        api_url = request.form.get('api_url', '').strip()
        model = request.form.get('model', '').strip()
        
        # 验证必填字段
        if not api_key:
            return render_template('deepseek_config.html', 
                                 error='API密钥不能为空',
                                 api_key=api_key,
                                 api_url=api_url,
                                 model=model)
        
        # 更新配置（仅用于显示，不保存到文件）
        deepseek_api_key = api_key
        deepseek_api_url = api_url if api_url else 'https://api.deepseek.com/v1/chat/completions'
        deepseek_model = model if model else 'deepseek-chat'
        
        return render_template('deepseek_config.html', 
                             success='DeepSeek API配置已更新（仅用于显示）',
                             api_key=deepseek_api_key,
                             api_url=deepseek_api_url,
                             model=deepseek_model)
    
    # GET请求，显示当前配置
    return render_template('deepseek_config.html',
                          api_key=deepseek_api_key,
                          api_url=deepseek_api_url,
                          model=deepseek_model)

@app.route('/teacher/test_deepseek', methods=['POST'])
def test_deepseek():
    """测试DeepSeek API连接"""
    if not session.get('teacher_logged_in'):
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    
    test_prompt = request.form.get('test_prompt', '你好，请简单介绍一下你自己。').strip()
    api_key = request.form.get('api_key', '').strip()
    api_url = request.form.get('api_url', 'https://api.deepseek.com/v1/chat/completions').strip()
    model = request.form.get('model', 'deepseek-chat').strip()
    
    if not test_prompt:
        return jsonify({'success': False, 'message': '测试提示词不能为空'}), 400
    
    if not api_key:
        return jsonify({'success': False, 'message': '请提供API密钥'}), 400
    
    # 调用API
    result, error = call_deepseek_api(api_key, api_url, model, test_prompt, max_tokens=200, temperature=0.7)
    
    if error:
        return jsonify({'success': False, 'message': f'API测试失败: {error}'})
    
    return jsonify({
        'success': True,
        'message': 'API测试成功',
        'response': result
    })

@app.route('/student/generate_ai_content', methods=['POST'])
def generate_ai_content():
    """学生端AI生成文档内容（只有组长可以使用）"""
    if not session.get('student_name'):
        return jsonify({'success': False, 'message': '请先登录'}), 401
    
    student_name = session['student_name']
    class_name = session['class_name']
    group_name = session['group_name']
    is_leader = session.get('is_leader', False)
    prompt = request.form.get('prompt', '').strip()
    
    # 检查是否为组长
    if not is_leader:
        return jsonify({'success': False, 'message': '只有组长可以使用AI功能'}), 403
    
    if not prompt:
        return jsonify({'success': False, 'message': '提示词不能为空'}), 400
    
    # 使用教师端配置的API密钥
    global deepseek_api_key, deepseek_api_url, deepseek_model
    
    if not deepseek_api_key:
        return jsonify({'success': False, 'message': '教师尚未配置DeepSeek API密钥，请先联系教师在控制台配置'}), 400
    
    # 构建更详细的提示词
    enhanced_prompt = f"""请根据以下要求生成Markdown格式的文档内容：

学生请求：{prompt}

请生成适合小组协作学习的文档内容，内容应该：
1. 结构清晰，有明确的章节划分
2. 包含学习目标、主要内容、讨论问题等部分
3. 使用Markdown格式，包含适当的标题、列表和强调
4. 内容适合中学生理解水平
5. 适合小组协作讨论和学习

请直接生成文档内容，不要添加额外的解释。"""
    
    # 调用API
    result, error = call_deepseek_api(deepseek_api_key, deepseek_api_url, deepseek_model, enhanced_prompt, max_tokens=1500, temperature=0.8)
    
    if error:
        return jsonify({'success': False, 'message': f'AI生成失败: {error}'})
    
    # 获取当前文档内容
    room_id = f"group_{class_name}_{group_name}"
    current_content = documents_data.get(room_id, "")
    
    # 将AI生成的内容添加到当前文档末尾
    if current_content:
        new_content = current_content + "\n\n---\n\n## AI生成内容（由组长生成）\n\n" + result
    else:
        new_content = result
    
    # 更新文档内容
    documents_data[room_id] = new_content
    
    # 通知该小组所有成员文档已更新
    socketio.emit('ai_content_generated', {
        'content': new_content,
        'student_name': student_name,
        'ai_content': result,
        'message': f'组长{student_name}使用AI生成了新的内容'
    }, room=room_id)
    
    return jsonify({
        'success': True,
        'message': 'AI内容生成成功',
        'content': result,
        'full_content': new_content
    })

# ==================== 学生端路由 ====================

@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    """学生登录"""
    if request.method == 'POST':
        student_name = request.form.get('student_name', '').strip()
        
        if not student_name:
            return render_template('student_login.html', error='请输入姓名', active_class=active_class)
        
        if not active_class:
            return render_template('student_login.html', error='当前没有激活的班级，请联系教师', active_class=active_class)
        
        # 在学生分组中查找
        found = False
        group_name = None
        is_leader = False
        
        if active_class in groups_data:
            for grp, group_info in groups_data[active_class].items():
                # 检查学生是否在该组中
                if 'students' in group_info and student_name in group_info['students']:
                    found = True
                    group_name = grp
                    # 检查是否为组长
                    if 'leader' in group_info and student_name == group_info['leader']:
                        is_leader = True
                    break
        
        if not found:
            return render_template('student_login.html', 
                                 error=f'在班级"{active_class}"中未找到姓名"{student_name}"，请确认姓名是否正确',
                                 active_class=active_class)
        
        # 登录成功，设置session
        session['student_name'] = student_name
        session['class_name'] = active_class
        session['group_name'] = group_name
        session['is_leader'] = is_leader
        
        return redirect(url_for('editor'))
    
    return render_template('student_login.html', active_class=active_class)

@app.route('/student/logout')
def student_logout():
    """学生登出"""
    session.pop('student_name', None)
    session.pop('class_name', None)
    session.pop('group_name', None)
    session.pop('is_leader', None)
    return redirect(url_for('student_login'))

@app.route('/editor')
def editor():
    """Markdown编辑器页面"""
    if not session.get('student_name'):
        return redirect(url_for('student_login'))
    
    student_name = session['student_name']
    class_name = session['class_name']
    group_name = session['group_name']
    is_leader = session.get('is_leader', False)
    
    # 获取或初始化文档内容
    room_id = f"group_{class_name}_{group_name}"
    document_content = documents_data.get(room_id, "")
    
    return render_template('editor.html',
                          student_name=student_name,
                          class_name=class_name,
                          group_name=group_name,
                          is_leader=is_leader,
                          document_content=document_content)

# ==================== Socket.IO事件处理 ====================

@socketio.on('connect')
def handle_connect():
    """客户端连接事件"""
    print(f"客户端连接: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开连接事件"""
    print(f"客户端断开: {request.sid}")

@socketio.on('join')
def handle_join(data):
    """学生加入小组房间"""
    student_name = data.get('student_name', '')
    class_name = data.get('class_name', '')
    group_name = data.get('group_name', '')
    
    if not all([student_name, class_name, group_name]):
        return
    
    room_id = f"group_{class_name}_{group_name}"
    join_room(room_id)
    
    # 获取当前房间的在线用户列表
    room_clients = socketio.server.manager.get_participants('/', room_id)
    online_users = []
    
    # 通知房间内其他用户有新成员加入
    emit('user_joined', {
        'student_name': student_name,
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }, room=room_id, include_self=False)
    
    # 发送当前文档内容给新加入的用户
    document_content = documents_data.get(room_id, "")
    emit('document_content', {
        'content': document_content
    }, room=request.sid)
    
    # 更新在线用户列表
    update_online_users(room_id)

@socketio.on('leave')
def handle_leave(data):
    """学生离开小组房间"""
    student_name = data.get('student_name', '')
    class_name = data.get('class_name', '')
    group_name = data.get('group_name', '')
    
    if not all([student_name, class_name, group_name]):
        return
    
    room_id = f"group_{class_name}_{group_name}"
    leave_room(room_id)
    
    # 通知房间内其他用户有成员离开
    emit('user_left', {
        'student_name': student_name,
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }, room=room_id)
    
    # 更新在线用户列表
    update_online_users(room_id)

@socketio.on('edit')
def handle_edit(data):
    """处理文档编辑事件"""
    student_name = data.get('student_name', '')
    class_name = data.get('class_name', '')
    group_name = data.get('group_name', '')
    content = data.get('content', '')
    
    if not all([student_name, class_name, group_name]):
        return
    
    room_id = f"group_{class_name}_{group_name}"
    
    # 更新文档内容（内存缓存）
    documents_data[room_id] = content
    
    # 可选：保存到文件（根据需求决定是否持久化）
    # save_documents()
    
    # 广播给房间内其他用户
    emit('document_updated', {
        'content': content,
        'student_name': student_name,
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }, room=room_id, include_self=False)

def update_online_users(room_id):
    """更新在线用户列表"""
    try:
        # 获取房间内的所有客户端
        room_clients = socketio.server.manager.get_participants('/', room_id)
        
        # 将生成器转换为列表以获取长度
        client_list = list(room_clients)
        
        # 发送更新后的在线用户列表
        emit('online_users_update', {
            'count': len(client_list)
        }, room=room_id)
    except Exception as e:
        print(f"更新在线用户列表失败: {e}")

# ==================== API接口 ====================

@app.route('/api/check_active_class')
def check_active_class():
    """检查当前激活的班级"""
    return jsonify({
        'active_class': active_class,
        'has_active_class': active_class is not None
    })

@app.route('/api/get_online_users/<class_name>/<group_name>')
def get_online_users(class_name, group_name):
    """获取指定小组的在线用户数"""
    room_id = f"group_{class_name}_{group_name}"
    try:
        room_clients = socketio.server.manager.get_participants('/', room_id)
        return jsonify({
            'success': True,
            'online_count': len(room_clients)
        })
    except:
        return jsonify({
            'success': True,
            'online_count': 0
        })

# ==================== 主程序入口 ====================

if __name__ == '__main__':
    print("=" * 60)
    print("局域网实时协作Markdown文档系统")
    print("=" * 60)
    print(f"教师登录密码: {teacher_password}")
    print(f"访问地址: http://0.0.0.0:5000")
    print(f"教师端: http://0.0.0.0:5000/teacher/login")
    print(f"学生端: http://0.0.0.0:5000/student/login")
    print("=" * 60)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
