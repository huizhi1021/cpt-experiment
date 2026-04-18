from flask import Flask, render_template, jsonify, request, session
import json
import random
import math
import sqlite3
import uuid
import os
from datetime import datetime
from functools import wraps
from scipy import stats

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cpt-experiment-2024-secret')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
DB_PATH = os.environ.get('DB_PATH', 'cpt_data.db')

# ─── 数据库初始化 ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE,
                participant_id TEXT,
                age TEXT,
                paradigm TEXT,
                total_trials INTEGER,
                target_probability REAL,
                start_time TEXT,
                end_time TEXT,
                status TEXT DEFAULT 'in_progress',
                hits INTEGER DEFAULT 0,
                misses INTEGER DEFAULT 0,
                false_alarms INTEGER DEFAULT 0,
                correct_rejections INTEGER DEFAULT 0,
                hit_rate REAL,
                false_alarm_rate REAL,
                d_prime REAL,
                beta REAL,
                hit_rt_mean REAL,
                hit_rt_std REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                trial_number INTEGER,
                stimulus TEXT,
                is_target INTEGER,
                has_response INTEGER,
                response_type TEXT,
                response_time_ms REAL,
                timestamp TEXT
            )
        ''')
        conn.commit()

init_db()

# ─── 会话状态（内存，按session_id隔离）──────────────────────────────────────

user_states = {}

def get_state(session_id):
    if session_id not in user_states:
        user_states[session_id] = {
            'phase': 'setup',
            'current_trial': 0,
            'is_practice': False,
            'practice_correct': 0,
            'config': {}
        }
    return user_states[session_id]

# ─── 主页 ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('cpt_academic.html')

# ─── 管理员后台 ───────────────────────────────────────────────────────────────

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    if data.get('password') == ADMIN_PASSWORD:
        session['admin'] = True
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error', 'message': '密码错误'}), 401

@app.route('/api/admin/data')
def admin_data():
    if not session.get('admin'):
        return jsonify({'error': 'unauthorized'}), 401

    with get_db() as conn:
        experiments = conn.execute('''
            SELECT * FROM experiments ORDER BY start_time DESC
        ''').fetchall()

    result = []
    for exp in experiments:
        result.append({
            'session_id': exp['session_id'][:8] + '...',
            'participant_id': exp['participant_id'] or '匿名',
            'age': exp['age'] or '-',
            'paradigm': exp['paradigm'],
            'total_trials': exp['total_trials'],
            'start_time': exp['start_time'],
            'status': exp['status'],
            'hits': exp['hits'],
            'misses': exp['misses'],
            'false_alarms': exp['false_alarms'],
            'correct_rejections': exp['correct_rejections'],
            'hit_rate': f"{round(exp['hit_rate'] * 100, 1)}%" if exp['hit_rate'] else '-',
            'false_alarm_rate': f"{round(exp['false_alarm_rate'] * 100, 1)}%" if exp['false_alarm_rate'] else '-',
            'd_prime': exp['d_prime'],
            'beta': exp['beta'],
            'hit_rt_mean': exp['hit_rt_mean'],
        })

    total = len(result)
    completed = sum(1 for r in result if r['status'] == 'completed')
    avg_d_prime = None
    d_primes = [exp['d_prime'] for exp in experiments if exp['d_prime'] is not None]
    if d_primes:
        avg_d_prime = round(sum(d_primes) / len(d_primes), 3)

    return jsonify({
        'experiments': result,
        'summary': {
            'total': total,
            'completed': completed,
            'avg_d_prime': avg_d_prime
        }
    })

@app.route('/api/admin/export')
def admin_export():
    if not session.get('admin'):
        return jsonify({'error': 'unauthorized'}), 401

    with get_db() as conn:
        experiments = conn.execute('SELECT * FROM experiments').fetchall()
        trials = conn.execute('SELECT * FROM trials').fetchall()

    data = {
        'export_time': datetime.now().isoformat(),
        'experiments': [dict(e) for e in experiments],
        'trials': [dict(t) for t in trials]
    }
    return jsonify(data)

# ─── 实验 API ─────────────────────────────────────────────────────────────────

@app.route('/api/init-session', methods=['POST'])
def init_session():
    session_id = str(uuid.uuid4())
    return jsonify({'session_id': session_id})

@app.route('/api/config', methods=['POST'])
def set_config():
    data = request.json
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'error': 'no session_id'}), 400

    state = get_state(session_id)
    state['config'] = {
        'paradigm': data.get('paradigm', 'AX-CPT'),
        'total_trials': int(data.get('total_trials', 100)),
        'target_probability': float(data.get('target_probability', 0.3)),
        'practice_trials': 10
    }
    state['participant_info'] = data.get('participant_info', {})

    # 写入数据库
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO experiments
            (session_id, participant_id, age, paradigm, total_trials, target_probability, start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            data.get('participant_info', {}).get('id', ''),
            data.get('participant_info', {}).get('age', ''),
            state['config']['paradigm'],
            state['config']['total_trials'],
            state['config']['target_probability'],
            datetime.now().isoformat()
        ))
        conn.commit()

    return jsonify({'status': 'configured'})

@app.route('/api/start-practice', methods=['POST'])
def start_practice():
    data = request.json
    session_id = data.get('session_id')
    state = get_state(session_id)
    state['phase'] = 'practice'
    state['is_practice'] = True
    state['current_trial'] = 0
    state['practice_correct'] = 0
    return jsonify({'status': 'practice_started'})

@app.route('/api/start-formal', methods=['POST'])
def start_formal():
    data = request.json
    session_id = data.get('session_id')
    state = get_state(session_id)
    state['phase'] = 'formal'
    state['is_practice'] = False
    state['current_trial'] = 0
    return jsonify({'status': 'formal_started'})

@app.route('/api/next-trial', methods=['GET'])
def get_next_trial():
    session_id = request.args.get('session_id')
    state = get_state(session_id)
    config = state['config']

    max_trials = config['practice_trials'] if state['is_practice'] else config['total_trials']

    if state['current_trial'] >= max_trials:
        return jsonify({'status': 'completed'})

    is_target = random.random() < config['target_probability']
    target_letter = 'X'

    if config['paradigm'] == 'AX-CPT':
        stimulus = target_letter if is_target else random.choice(
            [c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if c != target_letter]
        )
    else:
        stimulus = target_letter if is_target else 'O'

    state['current_trial'] += 1

    return jsonify({
        'status': 'trial',
        'trial_number': state['current_trial'],
        'total_trials': max_trials,
        'stimulus': stimulus,
        'is_target': is_target,
        'phase': state['phase']
    })

@app.route('/api/submit-response', methods=['POST'])
def submit_response():
    data = request.json
    session_id = data.get('session_id')
    state = get_state(session_id)

    is_target = data['is_target']
    has_response = data['has_response']

    if is_target:
        response_type = 'hit' if has_response else 'miss'
    else:
        response_type = 'false_alarm' if has_response else 'correct_rejection'

    if state['is_practice']:
        if response_type in ('hit', 'correct_rejection'):
            state['practice_correct'] += 1
    else:
        with get_db() as conn:
            conn.execute('''
                INSERT INTO trials
                (session_id, trial_number, stimulus, is_target, has_response, response_type, response_time_ms, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                state['current_trial'],
                data['stimulus'],
                1 if is_target else 0,
                1 if has_response else 0,
                response_type,
                data.get('response_time'),
                datetime.now().isoformat()
            ))
            conn.commit()

    return jsonify({'status': 'recorded'})

@app.route('/api/get-practice-results', methods=['GET'])
def get_practice_results():
    session_id = request.args.get('session_id')
    state = get_state(session_id)
    total = state['current_trial']
    correct = state['practice_correct']
    accuracy = (correct / total * 100) if total > 0 else 0
    return jsonify({'correct': correct, 'total': total, 'accuracy': round(accuracy, 1)})

@app.route('/api/get-formal-results', methods=['GET'])
def get_formal_results():
    session_id = request.args.get('session_id')

    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM trials WHERE session_id = ?', (session_id,)
        ).fetchall()

    trials = [dict(r) for r in rows]
    hits = sum(1 for t in trials if t['response_type'] == 'hit')
    misses = sum(1 for t in trials if t['response_type'] == 'miss')
    false_alarms = sum(1 for t in trials if t['response_type'] == 'false_alarm')
    correct_rejections = sum(1 for t in trials if t['response_type'] == 'correct_rejection')

    target_count = hits + misses
    non_target_count = false_alarms + correct_rejections

    hit_rate = max(0.01, min(0.99, hits / target_count)) if target_count > 0 else 0.5
    false_alarm_rate = max(0.01, min(0.99, false_alarms / non_target_count)) if non_target_count > 0 else 0.5

    z_hit = stats.norm.ppf(hit_rate)
    z_fa = stats.norm.ppf(false_alarm_rate)
    d_prime = round(z_hit - z_fa, 3)
    beta = round(math.exp(-(z_hit + z_fa) / 2), 3)

    hit_rts = [t['response_time_ms'] for t in trials if t['response_type'] == 'hit' and t['response_time_ms']]
    fa_rts = [t['response_time_ms'] for t in trials if t['response_type'] == 'false_alarm' and t['response_time_ms']]

    avg_hit_rt = sum(hit_rts) / len(hit_rts) if hit_rts else 0
    std_hit_rt = math.sqrt(sum((x - avg_hit_rt) ** 2 for x in hit_rts) / len(hit_rts)) if len(hit_rts) > 1 else 0
    avg_fa_rt = sum(fa_rts) / len(fa_rts) if fa_rts else 0
    consistency = round(1 - (std_hit_rt / avg_hit_rt), 3) if avg_hit_rt > 0 and std_hit_rt > 0 else 0

    # 更新数据库
    with get_db() as conn:
        conn.execute('''
            UPDATE experiments SET
                status = 'completed', end_time = ?,
                hits = ?, misses = ?, false_alarms = ?, correct_rejections = ?,
                hit_rate = ?, false_alarm_rate = ?,
                d_prime = ?, beta = ?,
                hit_rt_mean = ?, hit_rt_std = ?
            WHERE session_id = ?
        ''', (
            datetime.now().isoformat(),
            hits, misses, false_alarms, correct_rejections,
            hit_rate, false_alarm_rate,
            d_prime, beta,
            round(avg_hit_rt, 2), round(std_hit_rt, 2),
            session_id
        ))
        conn.commit()

    return jsonify({
        'performance_metrics': {
            'hits': hits, 'misses': misses,
            'false_alarms': false_alarms, 'correct_rejections': correct_rejections,
            'hit_rate': round(hit_rate, 3), 'false_alarm_rate': round(false_alarm_rate, 3)
        },
        'sdt_measures': {
            'd_prime': d_prime, 'beta': beta,
            'interpretation': f"d' = {d_prime}，β = {beta}"
        },
        'reaction_time_measures': {
            'hit_rt_mean_ms': round(avg_hit_rt, 2),
            'hit_rt_std_ms': round(std_hit_rt, 2),
            'false_alarm_rt_mean_ms': round(avg_fa_rt, 2),
            'consistency': consistency
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8003))
    app.run(debug=False, host='0.0.0.0', port=port)
