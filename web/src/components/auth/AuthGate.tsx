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
          background: '#f3f5f8',
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
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        gridTemplateColumns: 'minmax(320px, 1.1fr) minmax(360px, 0.9fr)',
        alignItems: 'stretch',
        padding: 0,
        background:
          'radial-gradient(circle at top, rgba(255,255,255,0.96), rgba(243,245,248,1) 58%)',
      }}
    >
      <div
        style={{
          display: 'grid',
          placeItems: 'center',
          padding: '48px 40px',
        }}
      >
        <div
          style={{
            maxWidth: 360,
            textAlign: 'left',
            width: '100%',
          }}
        >
          <Title
            level={1}
            style={{
              color: 'rgba(15, 23, 42, 0.92)',
              fontSize: 44,
              fontWeight: 700,
              letterSpacing: '-0.04em',
              lineHeight: 1.1,
              marginBottom: 12,
            }}
          >
            从任何想法开始
          </Title>
          <Paragraph
            style={{
              color: 'rgba(71, 85, 105, 0.72)',
              fontSize: 15,
              lineHeight: 1.7,
              margin: 0,
            }}
          >
            与你的 agents 一起展开工作。
          </Paragraph>
        </div>
      </div>
      <div
        style={{
          display: 'grid',
          placeItems: 'center',
          padding: '32px 20px',
        }}
      >
        <div style={{ width: '100%', maxWidth: 420 }}>
        {error ? (
          <div
            style={{
              color: 'rgba(220, 38, 38, 0.84)',
              fontSize: 12,
              marginBottom: 12,
              textAlign: 'center',
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
