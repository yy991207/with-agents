import { Button, Form, Input } from 'antd';

export interface LoginFormValue {
  username: string;
  password: string;
}

interface LoginPageProps {
  loading?: boolean;
  onSubmit: (value: LoginFormValue) => Promise<void> | void;
  onSwitchRegister: () => void;
}

export default function LoginPage({
  loading = false,
  onSubmit,
  onSwitchRegister,
}: LoginPageProps) {
  return (
    <div style={{ width: '100%' }}>
      {/* 标题 */}
      <div style={{ marginBottom: 28 }}>
        <h2
          style={{
            margin: 0,
            color: '#1f2937',
            fontSize: 22,
            fontWeight: 700,
            letterSpacing: '-0.03em',
            lineHeight: 1.2,
          }}
        >
          登录
        </h2>
        <p
          style={{
            margin: '6px 0 0',
            color: '#6b7280',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          输入账号和密码继续
        </p>
      </div>

      <Form<LoginFormValue> className="auth-form" layout="vertical" onFinish={onSubmit} requiredMark={false}>
        <Form.Item
          label="账号"
          name="username"
          rules={[{ required: true, message: '请输入账号' }]}
        >
          <Input autoComplete="username" placeholder="输入账号" />
        </Form.Item>
        <Form.Item
          label="密码"
          name="password"
          rules={[{ required: true, message: '请输入密码' }]}
        >
          <Input.Password autoComplete="current-password" placeholder="输入密码" />
        </Form.Item>
        <Button
          htmlType="submit"
          className="auth-btn-primary"
          block
          loading={loading}
          style={{ marginTop: 6 }}
        >
          登 录
        </Button>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 20 }}>
          <Button
            type="link"
            className="auth-switch-link"
            data-auth-switch="register"
            onClick={onSwitchRegister}
            style={{ paddingInline: 0 }}
          >
            没有账号？注 册
          </Button>
        </div>
      </Form>
    </div>
  );
}