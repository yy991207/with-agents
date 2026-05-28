import { Button, Form, Input } from 'antd';

export interface RegisterFormValue {
  username: string;
  password: string;
  confirmPassword: string;
}

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
          注册
        </h2>
        <p
          style={{
            margin: '6px 0 0',
            color: '#6b7280',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          创建你的工作空间
        </p>
      </div>

      <Form<RegisterFormValue> className="auth-form" layout="vertical" onFinish={onSubmit} requiredMark={false}>
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
          <Input.Password autoComplete="new-password" placeholder="输入密码" />
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
          <Input.Password autoComplete="new-password" placeholder="再次输入密码" />
        </Form.Item>
        <Button
          htmlType="submit"
          className="auth-btn-primary"
          block
          loading={loading}
          style={{ marginTop: 6 }}
        >
          注 册
        </Button>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: 20 }}>
          <Button
            type="link"
            className="auth-switch-link"
            data-auth-switch="login"
            onClick={onSwitchLogin}
            style={{ paddingInline: 0 }}
          >
            已有账号？登 录
          </Button>
        </div>
      </Form>
    </div>
  );
}