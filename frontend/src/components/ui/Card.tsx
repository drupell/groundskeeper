// Card primitives shared across pages. Visual contract:
//   <Card>
//     <CardHeader title="…" icon={…}>{optional badge}</CardHeader>
//     {body}
//     <CardFooter>{optional helper text}</CardFooter>
//   </Card>

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-xl border border-slate-800 bg-slate-950 p-6', className)}>
      {children}
    </div>
  )
}

export function CardHeader({
  title,
  icon,
  children,
}: {
  title: string
  icon?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="inline-flex items-center gap-2 text-base font-medium text-white">
        {icon && <span className="text-slate-500">{icon}</span>}
        {title}
      </h2>
      {children}
    </div>
  )
}

export function CardSubtitle({ children }: { children: ReactNode }) {
  return <p className="-mt-2 mb-4 text-sm text-slate-400">{children}</p>
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <p className="mt-4 text-xs text-slate-500">{children}</p>
}
