import { toast } from 'vue-sonner'

export function useToast() {
  return {
    success: (title: string, message?: string) =>
      toast.success(title, message ? { description: message } : undefined),
    error: (title: string, message?: string) =>
      toast.error(title, message ? { description: message } : undefined),
    info: (title: string, message?: string) =>
      toast(title, message ? { description: message } : undefined),
    warning: (title: string, message?: string) =>
      toast.warning(title, message ? { description: message } : undefined),
  }
}
