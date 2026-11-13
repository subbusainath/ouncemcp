const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 })

export const int = (n: number) => integer.format(n)
export const dec = (n: number) => decimal.format(n)
export const ms = (n: number) => `${decimal.format(n)} ms`
export const pct = (n: number | null | undefined) =>
  n == null ? "–" : `${Math.round(n * 100)}%`

export function time(seconds: number) {
  const d = new Date(seconds * 1000)
  return d.toISOString().replace("T", " ").slice(0, 23)
}

export function errorList(errors: Record<string, number>) {
  return Object.entries(errors).sort((a, b) => b[1] - a[1])
}
