import { vi } from 'vitest'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
})

URL.createObjectURL = vi.fn(() => 'blob:test-preview')
URL.revokeObjectURL = vi.fn()
