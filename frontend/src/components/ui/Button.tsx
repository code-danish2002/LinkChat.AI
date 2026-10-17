import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const variants: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-600/50 shadow-sm",
  secondary: "bg-white text-slate-800 ring-1 ring-slate-200 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700 dark:hover:bg-slate-700",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
  danger: "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40",
};
const sizes: Record<Size, string> = { sm: "h-8 px-2.5 text-xs gap-1.5", md: "h-10 px-4 text-sm gap-2" };

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    />
  );
}
