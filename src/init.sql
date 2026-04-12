-- ============================================================================
-- TaskPilot PostgreSQL Initialization Script
-- Версия: 1.0 (PoC)
-- ============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TABLES
-- ============================================================================

-- Groups table (отделы/команды)
CREATE TABLE IF NOT EXISTS groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    group_id UUID REFERENCES groups(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tasks table (основная таблица задач)
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id),
    group_id UUID NOT NULL REFERENCES groups(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'new',
    priority INTEGER DEFAULT 5,
    deadline TIMESTAMPTZ,
    problem TEXT,
    dependencies UUID[] DEFAULT '{}',
    source_message_id VARCHAR(255),
    -- embedding VECTOR(384),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Dependencies journal (журнал зависимостей между задачами)
CREATE TABLE IF NOT EXISTS dependencies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE(task_id, depends_on_id)
);

-- Messages/Chat history (история чата)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id),
    group_id UUID NOT NULL REFERENCES groups(id),
    content TEXT NOT NULL,
    role VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log (журнал аудита всех действий)
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100),
    resource_id UUID,
    details JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INDEXES (для производительности)
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_users_group_id ON users(group_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_group_id ON tasks(group_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
-- CREATE INDEX IF NOT EXISTS idx_tasks_embedding ON tasks USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_dependencies_task_id ON dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_dependencies_depends_on ON dependencies(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_group_id ON messages(group_id);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- ============================================================================
-- ROW LEVEL SECURITY (RLS) - Изоляция пользователей
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- RLS Policies for tasks (самое важное)
CREATE POLICY tasks_user_isolation ON tasks
    FOR ALL
    USING (
        group_id = current_setting('app.current_group_id', TRUE)::UUID
        OR user_id = current_setting('app.current_user_id', TRUE)::UUID
    );

CREATE POLICY tasks_user_insert ON tasks
    FOR INSERT
    WITH CHECK (
        user_id = current_setting('app.current_user_id', TRUE)::UUID
    );

-- RLS Policies for messages
CREATE POLICY messages_user_isolation ON messages
    FOR ALL
    USING (
        group_id = current_setting('app.current_group_id', TRUE)::UUID
    );

-- RLS Policies for dependencies
CREATE POLICY dependencies_user_isolation ON dependencies
    FOR ALL
    USING (
        task_id IN (
            SELECT id FROM tasks WHERE 
            group_id = current_setting('app.current_group_id', TRUE)::UUID
        )
    );

-- RLS Policies for users (видим только пользователей своей группы)
CREATE POLICY users_group_isolation ON users
    FOR SELECT
    USING (
        group_id = current_setting('app.current_group_id', TRUE)::UUID
        OR id = current_setting('app.current_user_id', TRUE)::UUID
    );

-- RLS Policies for audit_log (пользователь видит только свои записи)
CREATE POLICY audit_user_isolation ON audit_log
    FOR SELECT
    USING (
        user_id = current_setting('app.current_user_id', TRUE)::UUID
    );

-- ============================================================================
-- SEED DATA (тестовые данные для MVP)
-- ============================================================================

-- Default group
INSERT INTO groups (id, name, description) VALUES 
    ('00000000-0000-0000-0000-000000000001', 'Default', 'Группа по умолчанию для MVP')
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO taskpilot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO taskpilot;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO taskpilot;

-- ============================================================================
-- HELPER FUNCTIONS (для установки контекста RLS)
-- ============================================================================

CREATE OR REPLACE FUNCTION set_app_context(p_user_id UUID, p_group_id UUID)
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_user_id', p_user_id::TEXT, FALSE);
    PERFORM set_config('app.current_group_id', p_group_id::TEXT, FALSE);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION clear_app_context()
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_user_id', NULL, FALSE);
    PERFORM set_config('app.current_group_id', NULL, FALSE);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- END OF INIT SCRIPT
-- ============================================================================