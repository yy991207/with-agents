import { useState } from 'react';
import { Spin, Typography } from 'antd';

import LoginPage, { type LoginFormValue } from './LoginPage';
import RegisterPage, { type RegisterFormValue } from './RegisterPage';

const { Title, Paragraph } = Typography;

type AuthMode = 'login' | 'register';

interface AuthGateProps {
  children: React.ReactNode;
  authState: 'checking' | 'anonymous' | 'authenticated';
  initialMode?: AuthMode;
  onLogin: (payload: LoginFormValue) => Promise<void>;
  onRegister: (payload: RegisterFormValue) => Promise<void>;
}

export default function AuthGate({
  children,
  authState,
  initialMode = 'login',
  onLogin,
  onRegister,
}: AuthGateProps) {
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (authState === 'authenticated') {
    return <>{children}</>;
  }

  if (authState === 'checking') {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'grid',
          placeItems: 'center',
          background: '#111827',
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  const handleLogin = async (payload: LoginFormValue) => {
    setBusy(true);
    setError('');
    try {
      await onLogin(payload);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRegister = async (payload: RegisterFormValue) => {
    setBusy(true);
    setError('');
    try {
      await onRegister(payload);
      setMode('login');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-gate-root" style={{ minHeight: '100vh', display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(380px, 1fr)', alignItems: 'stretch' }}>
      {/* 左面板: 近黑底 + 品牌文案 */}
      <div
        style={{
          background: '#111827',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '56px 48px',
          position: 'relative',
        }}
      >
        {/* 装饰线 */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            width: 1,
            height: '100%',
            background: 'rgba(255,255,255,0.06)',
          }}
        />
        <div style={{ maxWidth: 380 }}>
          <Title
            level={1}
            style={{
              color: '#f9fafb',
              fontSize: 40,
              fontWeight: 700,
              letterSpacing: '-0.04em',
              lineHeight: 1.15,
              marginBottom: 16,
            }}
          >
            从任何想法开始
          </Title>
          <Paragraph
            style={{
              color: 'rgba(249,250,251,0.52)',
              fontSize: 15,
              lineHeight: 1.75,
              margin: 0,
            }}
          >
            与你的 agents 一起展开工作。
          </Paragraph>
          </div>
      </div>

      {/* 右面板: 浅灰底 + 表单 */}
      <div
        style={{
          background: '#f5f5f5',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '56px 48px',
        }}
      >
        <div style={{ width: '100%', maxWidth: 380, margin: '0 auto' }}>
          {error ? (
            <div
              style={{
                color: '#dc2626',
                fontSize: 12,
                marginBottom: 14,
                textAlign: 'center',
                background: '#fef2f2',
                borderRadius: 8,
                padding: '8px 12px',
              }}
            >
              {error}
            </div>
          ) : null}
          {mode === 'login' ? (
            <LoginPage
              loading={busy}
              onSubmit={handleLogin}
              onSwitchRegister={() => {
                setError('');
                setMode('register');
              }}
            />
          ) : (
            <RegisterPage
              loading={busy}
              onSubmit={handleRegister}
              onSwitchLogin={() => {
                setError('');
                setMode('login');
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}