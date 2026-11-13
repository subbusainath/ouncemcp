import { useEffect, useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"

import { Button } from "@/components/ui/button"

type Theme = "system" | "light" | "dark"
const KEY = "ouncemcp-report-theme"

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY)
    return value === "light" || value === "dark" ? value : "system"
  } catch {
    return "system"
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(stored)

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches)
      document.documentElement.classList.toggle("dark", dark)
      document.documentElement.style.colorScheme = dark ? "dark" : "light"
    }
    apply()
    media.addEventListener("change", apply)
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      // private windows: the choice lasts for this page only
    }
    return () => media.removeEventListener("change", apply)
  }, [theme])

  const next: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" }
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => setTheme(next[theme])}
      aria-label={`Theme: ${theme}. Switch to ${next[theme]}.`}
    >
      <Icon />
      <span className="capitalize">{theme}</span>
    </Button>
  )
}
