import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Login from './Login'

describe('Login', () => {
  afterEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('offers a single "Google로 계속하기" sign-in button with no ChatGPT/OpenAI copy', () => {
    const { container } = render(<Login />)

    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Google로 계속하기' })).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/ChatGPT|OpenAI|플랜|사용량/)
  })

  it('navigates to the backend login URL when the button is pressed', () => {
    const navigate = vi.fn()
    render(<Login navigate={navigate} />)

    const button = screen.getByRole('button', { name: 'Google로 계속하기' })
    fireEvent.click(button)

    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('/api/auth/login')
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('Google로 이동하는 중…')
  })

  it.each([
    ['login_not_verified', '로그인 요청을 확인할 수 없어요'], // Req 9.4
    ['sign_in_not_completed', '로그인이 완료되지 않았어요'], // Req 9.5
    ['sign_in_service_unreachable', '로그인을 완료할 수 없어요'], // Req 9.7
    ['account_not_verified', '계정을 확인할 수 없어요'], // Req 9.10
    ['sign_in_unavailable', '지금은 로그인할 수 없어요'],
  ])('shows callback error %s with a retry that starts a new login', (code, title) => {
    window.history.replaceState({}, '', `/?auth_error=${code}`)
    const navigate = vi.fn()
    const { container } = render(<Login navigate={navigate} />)

    expect(screen.getByRole('alert')).toHaveTextContent(title)
    expect(container.textContent).not.toMatch(/ChatGPT|OpenAI/)

    fireEvent.click(screen.getByRole('button', { name: 'Google로 계속하기' }))
    expect(navigate).toHaveBeenCalledWith('/api/auth/login')
  })

  it('ignores unknown callback error codes', () => {
    window.history.replaceState({}, '', '/?auth_error=plan_ineligible')
    render(<Login />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
