import { Button, Form, Input, Typography } from 'antd';

export interface LoginFormValue {
  username: string;
  password: string;
}

const { Title, Text } = Typography;

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
    <div
      style={{
        width: '100%',
        maxWidth: 420,
      }}
    >
      <div style={{ marginBottom: 20, textAlign: 'center' }}>
        <Title
          level={4}
          style={{
            margin: 0,
            color: 'rgba(15, 23, 42, 0.92)',
            fontWeight: 700,
            letterSpacing: '-0.02em',
          }}
        >
          登录
        </Title>
      </div>
      <Form<LoginFormValue> layout="vertical" onFinish={onSubmit}>
        <Form.Item
          label="账号"
          name="username"
          rules={[{ required: true, message: '请输入账号' }]}
        >
          <Input autoComplete="username" size="large" />
        </Form.Item>
        <Form.Item
          label="密码"
          name="password"
          rules={[{ required: true, message: '请输入密码' }]}
        >
          <Input.Password autoComplete="current-password" size="large" />
        </Form.Item>
        <Button
          htmlType="submit"
          type="primary"
          block
          loading={loading}
          style={{ marginTop: 4, height: 40 }}
        >
          登 录
        </Button>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            <Button
              type="link"
              data-auth-switch="register"
              onClick={onSwitchRegister}
              style={{ paddingInline: 6, color: 'rgba(15, 23, 42, 0.62)' }}
            >
              注 册
            </Button>
          </Text>
        </div>
      </Form>
    </div>
  );
}
