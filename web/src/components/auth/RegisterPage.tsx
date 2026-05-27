import { Button, Form, Input, Typography } from 'antd';

export interface RegisterFormValue {
  tenantName: string;
  username: string;
  password: string;
  confirmPassword: string;
}

const { Title, Text } = Typography;

interface RegisterPageProps {
  loading?: boolean;
  onSubmit: (value: RegisterFormValue) => Promise<void> | void;
  onSwitchLogin: () => void;
}

export default function RegisterPage({
  loading = false,
  onSubmit,
  onSwitchLogin,
}: RegisterPageProps) {
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
          注册
        </Title>
      </div>
      <Form<RegisterFormValue> layout="vertical" onFinish={onSubmit}>
        <Form.Item
          label="租户名"
          name="tenantName"
          rules={[{ required: true, message: '请输入租户名' }]}
        >
          <Input size="large" />
        </Form.Item>
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
          <Input.Password autoComplete="new-password" size="large" />
        </Form.Item>
        <Form.Item
          label="确认密码"
          name="confirmPassword"
          dependencies={['password']}
          rules={[
            { required: true, message: '请确认密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('password') === value) {
                  return Promise.resolve();
                }
                return Promise.reject(new Error('两次密码不一致'));
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" size="large" />
        </Form.Item>
        <Button
          htmlType="submit"
          type="primary"
          block
          loading={loading}
          style={{ marginTop: 4, height: 40 }}
        >
          注 册
        </Button>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            <Button
              type="link"
              data-auth-switch="login"
              onClick={onSwitchLogin}
              style={{ paddingInline: 6, color: 'rgba(15, 23, 42, 0.62)' }}
            >
              登 录
            </Button>
          </Text>
        </div>
      </Form>
    </div>
  );
}
