const { execFileSync } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

// Only for the disposable database created by scripts/e2e_server.py.
function seedPersonalAccount() {
  const database = process.env.OFFICIUM_E2E_DATABASE || path.join(os.tmpdir(), 'officium-playwright.sqlite');
  if (path.basename(database) !== 'officium-playwright.sqlite' || !fs.existsSync(database)) {
    throw new Error('Expected the disposable e2e database. Set OFFICIUM_E2E_DATABASE to its path.');
  }
  const localPython = path.resolve('.venv/Scripts/python.exe');
  const python = process.env.PYTHON || (fs.existsSync(localPython) ? localPython : 'python');
  const code = `
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
row = connection.execute('SELECT id FROM users WHERE telegram_id = -1000000001').fetchone()
if not row: raise RuntimeError('Log in using /dev-login/user first')
uid = row[0]
connection.execute('UPDATE users SET telegram_id=?, hide_amounts_on_open=0, notification_timezone=NULL, notification_delivery_time=NULL WHERE id=?', (8000000000 + uid, uid))
connection.execute("INSERT INTO expenses (user_id, amount, category, title, expense_date, is_monthly) VALUES (?, 987654.32, 'transport', 'Приватный расход', date('now'), 0)", (uid,))
connection.commit()
connection.close()
`;
  execFileSync(python, ['-c', code, database]);
}

module.exports = { seedPersonalAccount };
