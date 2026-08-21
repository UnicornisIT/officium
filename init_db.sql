-- ============================================================
-- Скрипт создания базы данных debt_manager
-- Выполните этот скрипт в MySQL перед запуском приложения
-- ============================================================

CREATE DATABASE IF NOT EXISTS debt_manager
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE debt_manager;

-- Таблица пользователей для Telegram-авторизации
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    username VARCHAR(80) NULL,
    first_name VARCHAR(100) NULL,
    last_name VARCHAR(100) NULL,
    photo_url VARCHAR(255) NULL,
    auth_date DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    role ENUM('user','admin','superadmin') NOT NULL DEFAULT 'user',
    is_blocked TINYINT(1) NOT NULL DEFAULT 0,
    last_login_ip VARCHAR(100) NULL,
    last_user_agent TEXT NULL,
    login_count INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Таблица долгов
CREATE TABLE IF NOT EXISTS debts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT             NOT NULL COMMENT 'ID пользователя',
    bank_name       VARCHAR(100)    NOT NULL COMMENT 'Название банка',
    debt_type       ENUM('credit_card', 'split') NOT NULL COMMENT 'Тип: кредитная карта или сплит',
    product_name    VARCHAR(150)    NOT NULL COMMENT 'Название продукта/карты',
    total_amount    DECIMAL(12, 2)  NOT NULL COMMENT 'Первоначальная сумма долга',
    remaining_amount DECIMAL(12, 2) NOT NULL COMMENT 'Текущий остаток долга',
    minimum_payment DECIMAL(12, 2)  NULL     COMMENT 'Минимальный ежемесячный платеж',
    interest_rate   DECIMAL(5, 2)   NULL     COMMENT 'Процентная ставка в год',
    next_payment_date DATE          NULL     COMMENT 'Дата следующего платежа',
    comment         TEXT            NULL     COMMENT 'Комментарий',
    status          ENUM('active', 'archived') NOT NULL DEFAULT 'active' COMMENT 'Статус',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_next_payment (next_payment_date),
    INDEX idx_bank (bank_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Таблица платежей
CREATE TABLE IF NOT EXISTS payments (
    id                       INT AUTO_INCREMENT PRIMARY KEY,
    debt_id                  INT             NOT NULL COMMENT 'ID долга',
    amount                   DECIMAL(12, 2)  NOT NULL COMMENT 'Сумма платежа',
    payment_date             DATE            NOT NULL COMMENT 'Дата платежа',
    comment                  TEXT            NULL     COMMENT 'Комментарий к платежу',
    remaining_after_payment  DECIMAL(12, 2)  NOT NULL COMMENT 'Остаток долга после платежа',
    created_at               DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (debt_id) REFERENCES debts(id) ON DELETE CASCADE,
    INDEX idx_debt_id (debt_id),
    INDEX idx_payment_date (payment_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Таблица доходов
CREATE TABLE IF NOT EXISTS incomes (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT             NOT NULL COMMENT 'ID пользователя',
    amount       DECIMAL(12, 2)  NOT NULL COMMENT 'Сумма дохода',
    category     ENUM('salary', 'advance', 'side_job', 'debt_return', 'bonus', 'scholarship', 'vacation_pay', 'goal_withdrawal', 'other') NOT NULL COMMENT 'Категория дохода',
    source       VARCHAR(150)    NULL COMMENT 'Источник дохода',
    income_date  DATE            NOT NULL COMMENT 'Дата поступления',
    comment      TEXT            NULL COMMENT 'Комментарий',
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_incomes_user_id (user_id),
    INDEX idx_incomes_date (income_date),
    INDEX idx_incomes_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Таблица расходов
CREATE TABLE IF NOT EXISTS expenses (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT             NOT NULL COMMENT 'ID пользователя',
    amount         DECIMAL(12, 2)  NOT NULL COMMENT 'Сумма расхода',
    category       ENUM('products', 'transport', 'communication', 'rent', 'loans', 'restaurants', 'entertainment', 'health', 'education', 'clothing', 'subscriptions', 'savings', 'other') NOT NULL COMMENT 'Категория расхода',
    title          VARCHAR(150)    NOT NULL COMMENT 'Название расхода',
    expense_date   DATE            NOT NULL COMMENT 'Дата расхода',
    payment_method VARCHAR(80)     NULL COMMENT 'Способ оплаты',
    comment        TEXT            NULL COMMENT 'Комментарий',
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_expenses_user_id (user_id),
    INDEX idx_expenses_date (expense_date),
    INDEX idx_expenses_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS app_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    `key` VARCHAR(100) NOT NULL UNIQUE,
    `value` TEXT NULL,
    description TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS financial_plan_preferences (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    living_minimum DECIMAL(12, 2) NOT NULL DEFAULT 20000,
    desired_monthly_savings DECIMAL(12, 2) NOT NULL DEFAULT 5000,
    emergency_fund_target_amount DECIMAL(12, 2) NOT NULL DEFAULT 30000,
    emergency_fund_target_mode ENUM('fixed', 'one_month', 'three_months') NOT NULL DEFAULT 'fixed',
    strategy ENUM('safe', 'balanced', 'aggressive') NOT NULL DEFAULT 'balanced',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS emergency_fund_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    transaction_type ENUM('deposit', 'withdrawal') NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    transaction_date DATE NOT NULL,
    comment VARCHAR(255) NULL,
    expense_id INT NULL UNIQUE,
    income_id INT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_emergency_fund_transaction_amount_positive CHECK (amount > 0),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE SET NULL,
    FOREIGN KEY (income_id) REFERENCES incomes(id) ON DELETE SET NULL,
    INDEX ix_emergency_fund_transactions_user_date (user_id, transaction_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS financial_goals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(120) NOT NULL,
    target_amount DECIMAL(12, 2) NOT NULL,
    monthly_contribution DECIMAL(12, 2) NOT NULL DEFAULT 0,
    target_date DATE NULL,
    note VARCHAR(500) NULL,
    priority INT NOT NULL DEFAULT 2,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT ck_financial_goal_target_positive CHECK (target_amount > 0),
    CONSTRAINT ck_financial_goal_monthly_nonnegative CHECK (monthly_contribution >= 0),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX ix_financial_goals_user_priority (user_id, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS financial_goal_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    goal_id INT NOT NULL,
    transaction_type ENUM('deposit', 'withdrawal') NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    transaction_date DATE NOT NULL,
    comment VARCHAR(255) NULL,
    expense_id INT NULL UNIQUE,
    income_id INT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_financial_goal_transaction_amount_positive CHECK (amount > 0),
    FOREIGN KEY (goal_id) REFERENCES financial_goals(id) ON DELETE CASCADE,
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE SET NULL,
    FOREIGN KEY (income_id) REFERENCES incomes(id) ON DELETE SET NULL,
    INDEX ix_financial_goal_transactions_goal_date (goal_id, transaction_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dictionary_entries (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dictionary_type ENUM('bank', 'debt_type', 'debt_category', 'status', 'comment_template', 'interest_rate', 'product_type') NOT NULL,
    value VARCHAR(150) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_dictionary_type_value (dictionary_type, value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS activity_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NULL,
    entity_id INT NULL,
    description TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_activity_user_id (user_id),
    INDEX idx_activity_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
