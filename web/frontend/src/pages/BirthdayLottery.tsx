import { useState, useRef, useCallback, useEffect } from 'react'
import { Button, Typography, ConfigProvider, Modal } from 'antd'
import { DownloadOutlined, GiftOutlined } from '@ant-design/icons'

const { Title, Text } = Typography

// ── Slot symbols ──────────────────────────────────────────────────────
const SYMBOLS = ['🍒', '🍋', '🔔', '💎', '⭐', '🍀', '🎰', '🎲']
const WIN_DIGITS = ['4️⃣', '2️⃣', '3️⃣'] // winning combo per reel
const REEL_COUNT = 3
const VISIBLE_ROWS = 3          // show 3 rows per reel (top, center, bottom)
const SYMBOL_HEIGHT = 90        // px per symbol cell
const SPIN_DURATION_BASE = 2500 // ms – first reel stops here
const SPIN_STAGGER = 1200       // ms extra per subsequent reel

// ── Theme colors (matching main app) ─────────────────────────────────
const C = {
  primary: '#9a856b',
  primaryDark: '#7a6a55',
  primaryLight: '#c1ae94',
  bg: '#f5f2ee',
  bgWarm: '#faf8f5',
  container: '#ffffff',
  text: '#2c2c2c',
  textSecondary: '#53565A',
  textMuted: '#888888',
  border: '#e0d8cf',
  borderLight: '#ece6de',
}

// ── Helpers ───────────────────────────────────────────────────────────
/** Build a long strip of random symbols with a guaranteed win digit at `winIdx` */
function buildStrip(length: number, winIdx: number, winSymbol: string): string[] {
  const strip: string[] = []
  for (let i = 0; i < length; i++) {
    if (i === winIdx) {
      strip.push(winSymbol)
    } else {
      strip.push(SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)])
    }
  }
  return strip
}

// ── Component ─────────────────────────────────────────────────────────
export default function BirthdayLottery() {
  const [phase, setPhase] = useState<'idle' | 'spinning' | 'won' | 'pdf'>('idle')
  const [reelStrips, setReelStrips] = useState<string[][]>(() =>
    WIN_DIGITS.map((sym) => buildStrip(60, 30, sym))
  )
  const [reelOffsets, setReelOffsets] = useState<number[]>([0, 0, 0])
  const [stoppedReels, setStoppedReels] = useState<boolean[]>([false, false, false])
  const [showConfetti, setShowConfetti] = useState(false)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState(false)
  const animFrameRef = useRef<number>(0)
  const startTimeRef = useRef<number>(0)
  const reelStopTargets = useRef<number[]>([0, 0, 0])

  // ── Spin animation loop ─────────────────────────────────────────────
  const animate = useCallback((timestamp: number) => {
    if (!startTimeRef.current) startTimeRef.current = timestamp
    const elapsed = timestamp - startTimeRef.current

    const newOffsets: number[] = []
    const newStopped: boolean[] = []

    for (let r = 0; r < REEL_COUNT; r++) {
      const reelDuration = SPIN_DURATION_BASE + r * SPIN_STAGGER
      if (elapsed >= reelDuration) {
        newOffsets.push(reelStopTargets.current[r])
        newStopped.push(true)
      } else {
        const progress = elapsed / reelDuration
        const eased = 1 - Math.pow(1 - progress, 3)
        const totalTravel = reelStopTargets.current[r]
        newOffsets.push(eased * totalTravel)
        newStopped.push(false)
      }
    }

    setReelOffsets(newOffsets)
    setStoppedReels(newStopped)

    if (newStopped.every(Boolean)) {
      setTimeout(() => {
        setShowConfetti(true)
        setPhase('won')
      }, 400)
      return
    }

    animFrameRef.current = requestAnimationFrame(animate)
  }, [])

  // ── Pull the lever ──────────────────────────────────────────────────
  const handleSpin = useCallback(() => {
    if (phase !== 'idle') return
    setPhase('spinning')
    setShowConfetti(false)
    setStoppedReels([false, false, false])

    const stripLen = 60
    const winIdx = 45
    const strips = WIN_DIGITS.map((sym) => buildStrip(stripLen, winIdx, sym))
    setReelStrips(strips)

    const targetSymbolTop = winIdx - 1
    const targetPx = targetSymbolTop * SYMBOL_HEIGHT
    reelStopTargets.current = [targetPx, targetPx, targetPx]

    setReelOffsets([0, 0, 0])
    startTimeRef.current = 0
    animFrameRef.current = requestAnimationFrame(animate)
  }, [phase, animate])

  useEffect(() => {
    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
    }
  }, [])

  // ── Show PDF after win ──────────────────────────────────────────────
  const handleRevealPrize = useCallback(() => {
    setPhase('pdf')
    fetch('/api/birthday/pdf')
      .then(res => {
        if (!res.ok) throw new Error('PDF not available yet')
        return res.blob()
      })
      .then(blob => {
        setPdfUrl(URL.createObjectURL(blob))
      })
      .catch(() => {
        setPdfError(true)
      })
  }, [])

  // ── Download & shutdown ─────────────────────────────────────────────
  const handleDownload = useCallback(() => {
    if (!pdfUrl) return
    const a = document.createElement('a')
    a.href = pdfUrl
    a.download = 'happy_birthday_dr_leeven.pdf'
    a.click()

    setTimeout(() => {
      Modal.success({
        title: 'Thank you for participating!',
        content: 'Happy Birthday Dr. Leeven! This page will now close.',
        okText: 'Goodbye',
        onOk: () => {
          window.close()
          setTimeout(() => {
            document.body.innerHTML = `
              <div style="display:flex;align-items:center;justify-content:center;height:100vh;
                          background:${C.bg};color:${C.primary};font-family:'Segoe UI',Roboto,sans-serif;text-align:center">
                <div>
                  <h1 style="font-size:2.5rem;color:${C.primary}">Happy Birthday Dr. Leeven!</h1>
                  <p style="font-size:1.1rem;color:${C.textMuted}">You may close this tab now.</p>
                </div>
              </div>
            `
          }, 300)
        },
      })
    }, 1000)
  }, [pdfUrl])

  // ── Confetti particles ──────────────────────────────────────────────
  // Confetti: two waves – initial burst + continuous rain
  const confettiElements = showConfetti
    ? Array.from({ length: 120 }, (_, i) => {
        const left = Math.random() * 100
        const delay = i < 60 ? Math.random() * 0.8 : 1.5 + Math.random() * 3
        const duration = 2 + Math.random() * 3
        const colors = [C.primary, C.primaryLight, C.primaryDark, '#d4c4a8', '#b8a080', '#e8ddd0', '#c9b896', '#a89070']
        const color = colors[i % colors.length]
        const size = 5 + Math.random() * 10
        const sway = (Math.random() - 0.5) * 200
        return (
          <div
            key={i}
            style={{
              position: 'fixed',
              left: `${left}%`,
              top: '-20px',
              width: size,
              height: i % 3 === 0 ? size * 0.4 : size,
              backgroundColor: color,
              borderRadius: i % 4 === 0 ? '50%' : i % 4 === 1 ? '2px' : '1px',
              animation: `confettiFall ${duration}s ${delay}s ease-in forwards`,
              transform: `translateX(${sway}px)`,
              zIndex: 9999,
              pointerEvents: 'none',
              opacity: 0.9,
            }}
          />
        )
      })
    : null

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <ConfigProvider
      theme={{
        token: {
          fontFamily: '"Segoe UI", Roboto, "Noto Sans HK", -apple-system, BlinkMacSystemFont, sans-serif',
          colorPrimary: C.primary,
          colorBgContainer: C.container,
          colorText: C.text,
          borderRadius: 8,
        },
      }}
    >
      <style>{`
        @keyframes confettiFall {
          0% { transform: translateY(0) rotate(0deg); opacity: 1; }
          100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
        }
        @keyframes pulseGlow {
          0%, 100% { box-shadow: 0 0 15px rgba(154,133,107,0.2); }
          50% { box-shadow: 0 0 30px rgba(154,133,107,0.4), 0 0 60px rgba(154,133,107,0.15); }
        }
        @keyframes jackpotFlash {
          0%, 100% { color: ${C.primary}; text-shadow: 0 0 15px rgba(154,133,107,0.4); transform: scale(1); }
          50% { color: ${C.primaryDark}; text-shadow: 0 0 25px rgba(122,106,85,0.6), 0 0 50px rgba(154,133,107,0.2); transform: scale(1.03); }
        }
        @keyframes bounceIn {
          0% { transform: scale(0.3); opacity: 0; }
          50% { transform: scale(1.08); opacity: 1; }
          70% { transform: scale(0.95); }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes starBurst {
          0% { transform: scale(0) rotate(0deg); opacity: 1; }
          50% { transform: scale(1.2) rotate(180deg); opacity: 0.8; }
          100% { transform: scale(0) rotate(360deg); opacity: 0; }
        }
        @keyframes slideUp {
          0% { transform: translateY(20px); opacity: 0; }
          100% { transform: translateY(0); opacity: 1; }
        }
        @keyframes celebratePulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); }
        }
        @keyframes floatUp {
          0% { transform: translateY(0); }
          50% { transform: translateY(-10px); }
          100% { transform: translateY(0); }
        }
        @keyframes shimmer {
          0% { background-position: -200% center; }
          100% { background-position: 200% center; }
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      {confettiElements}

      <div
        style={{
          minHeight: '100vh',
          background: `linear-gradient(160deg, ${C.bg} 0%, ${C.bgWarm} 40%, ${C.bg} 70%, #ede5da 100%)`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '20px',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Decorative background orbs */}
        <div style={{
          position: 'absolute', top: '10%', left: '10%', width: 300, height: 300,
          borderRadius: '50%', background: `radial-gradient(circle, rgba(154,133,107,0.08) 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />
        <div style={{
          position: 'absolute', bottom: '10%', right: '10%', width: 400, height: 400,
          borderRadius: '50%', background: `radial-gradient(circle, rgba(193,174,148,0.1) 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />

        {/* ── Title Section ── */}
        <div style={{ textAlign: 'center', marginBottom: 40, zIndex: 1, maxWidth: 700 }}>
          <div style={{ fontSize: '3rem', marginBottom: 12, animation: 'floatUp 3s ease-in-out infinite' }}>
            🎂
          </div>
          <Title
            level={1}
            style={{
              color: C.primary,
              margin: 0,
              fontSize: 'clamp(1.8rem, 5vw, 3rem)',
              fontWeight: 800,
              letterSpacing: 2,
              textShadow: `0 0 20px rgba(154,133,107,0.2)`,
              background: `linear-gradient(90deg, ${C.primaryDark}, ${C.primaryLight}, ${C.primaryDark})`,
              backgroundSize: '200% auto',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              animation: 'shimmer 3s linear infinite',
            }}
          >
            Happy Birthday!<br />Doctor Leeven
          </Title>

          {/* Decorative divider */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            gap: 16, margin: '20px 0',
          }}>
            <div style={{ height: 1, width: 60, background: `linear-gradient(90deg, transparent, ${C.border})` }} />
            <Text style={{ color: C.primaryLight, fontSize: '0.85rem', letterSpacing: 4, textTransform: 'uppercase', fontWeight: 600 }}>
              The 30th Year of Your Wonderful Being
            </Text>
            <div style={{ height: 1, width: 60, background: `linear-gradient(270deg, transparent, ${C.border})` }} />
          </div>

          <Text
            style={{
              color: C.textMuted,
              fontSize: 'clamp(0.85rem, 2vw, 1.05rem)',
              display: 'block',
              fontWeight: 400,
              fontStyle: 'italic',
              letterSpacing: 0.5,
            }}
          >
            Please Participate in the Ultimate River Delta Lottery for Game
          </Text>
        </div>

        {/* ── Slot Machine ── */}
        {phase !== 'pdf' && (
          <div
            style={{
              background: C.container,
              border: `2px solid ${C.border}`,
              borderRadius: 16,
              padding: '28px 24px 24px',
              animation: phase === 'won' ? 'pulseGlow 1.5s ease-in-out infinite' : undefined,
              boxShadow: '0 4px 24px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)',
              zIndex: 1,
              maxWidth: 500,
              width: '100%',
            }}
          >

            {/* Reels container */}
            <div
              style={{
                display: 'flex',
                gap: 12,
                justifyContent: 'center',
                marginBottom: 24,
                padding: '12px',
                background: C.bg,
                borderRadius: 12,
                border: `1px solid ${C.borderLight}`,
              }}
            >
              {[0, 1, 2].map(reelIdx => (
                <div
                  key={reelIdx}
                  style={{
                    width: 110,
                    height: SYMBOL_HEIGHT * VISIBLE_ROWS,
                    overflow: 'hidden',
                    borderRadius: 10,
                    background: C.container,
                    border: `2px solid ${stoppedReels[reelIdx] && phase === 'won' ? C.primary : C.borderLight}`,
                    position: 'relative',
                    transition: 'border-color 0.3s',
                    boxShadow: stoppedReels[reelIdx] && phase === 'won'
                      ? `0 0 12px rgba(154,133,107,0.25)` : 'inset 0 1px 4px rgba(0,0,0,0.05)',
                  }}
                >
                  {/* Top/bottom gradient overlays */}
                  <div style={{
                    position: 'absolute', top: 0, left: 0, right: 0, height: SYMBOL_HEIGHT * 0.8,
                    background: `linear-gradient(180deg, rgba(255,255,255,0.9), transparent)`,
                    zIndex: 2, pointerEvents: 'none',
                  }} />
                  <div style={{
                    position: 'absolute', bottom: 0, left: 0, right: 0, height: SYMBOL_HEIGHT * 0.8,
                    background: `linear-gradient(0deg, rgba(255,255,255,0.9), transparent)`,
                    zIndex: 2, pointerEvents: 'none',
                  }} />
                  {/* Center line indicator */}
                  <div style={{
                    position: 'absolute', top: SYMBOL_HEIGHT, left: 0, right: 0, height: SYMBOL_HEIGHT,
                    border: `1px solid ${C.borderLight}`,
                    borderLeft: 'none', borderRight: 'none',
                    zIndex: 3, pointerEvents: 'none',
                  }} />

                  {/* Scrolling strip */}
                  <div
                    style={{
                      transform: `translateY(-${reelOffsets[reelIdx]}px)`,
                      transition: phase === 'idle' ? 'none' : undefined,
                    }}
                  >
                    {reelStrips[reelIdx].map((sym, i) => (
                      <div
                        key={i}
                        style={{
                          height: SYMBOL_HEIGHT,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: '3rem',
                          filter: phase === 'spinning' && !stoppedReels[reelIdx]
                            ? 'blur(1px)' : 'none',
                          transition: 'filter 0.3s',
                        }}
                      >
                        {sym}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* Win message */}
            {phase === 'won' && (
              <div style={{ textAlign: 'center', marginBottom: 16, position: 'relative' }}>
                {/* Celebratory emoji ring */}
                <div style={{
                  display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 12,
                  animation: 'bounceIn 0.6s ease-out',
                }}>
                  {['🎉', '🥳', '🎊', '🍾', '🎈'].map((e, i) => (
                    <span key={i} style={{
                      fontSize: '1.8rem',
                      animation: `celebratePulse 1.2s ${i * 0.15}s ease-in-out infinite`,
                      display: 'inline-block',
                    }}>{e}</span>
                  ))}
                </div>

                <Title
                  level={2}
                  style={{
                    margin: 0,
                    animation: 'jackpotFlash 1.2s ease-in-out infinite',
                    fontSize: 'clamp(1.8rem, 5vw, 2.8rem)',
                    fontWeight: 900,
                    letterSpacing: 3,
                  }}
                >
                  JACKPOT!
                </Title>

                {/* Large winning numbers */}
                <div style={{
                  display: 'flex', justifyContent: 'center', gap: 12, margin: '12px 0',
                  animation: 'bounceIn 0.8s 0.3s ease-out both',
                }}>
                  {['4', '2', '3'].map((digit, i) => (
                    <div key={i} style={{
                      width: 52, height: 52, borderRadius: 10,
                      background: `linear-gradient(135deg, ${C.primary}, ${C.primaryDark})`,
                      color: '#fff', fontSize: '1.6rem', fontWeight: 900,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      boxShadow: `0 3px 12px rgba(154,133,107,0.35)`,
                      animation: `celebratePulse 1s ${0.5 + i * 0.2}s ease-in-out infinite`,
                    }}>
                      {digit}
                    </div>
                  ))}
                </div>

                <Text style={{
                  color: C.textSecondary, fontSize: '1.05rem', display: 'block', marginTop: 10,
                  animation: 'slideUp 0.6s 0.6s ease-out both',
                  fontWeight: 500,
                }}>
                  Congratulations, Dr. Leeven!
                </Text>
                <Text style={{
                  color: C.textMuted, fontSize: '0.9rem', display: 'block', marginTop: 4,
                  animation: 'slideUp 0.6s 0.8s ease-out both',
                }}>
                  You've won the ultimate birthday prize!
                </Text>
              </div>
            )}

            {/* Action button */}
            <div style={{ textAlign: 'center' }}>
              {phase === 'idle' && (
                <Button
                  type="primary"
                  size="large"
                  icon={<GiftOutlined />}
                  onClick={handleSpin}
                  style={{
                    height: 56,
                    fontSize: '1.2rem',
                    fontWeight: 700,
                    paddingInline: 48,
                    background: `linear-gradient(135deg, ${C.primary}, ${C.primaryDark})`,
                    border: 'none',
                    color: '#ffffff',
                    boxShadow: '0 4px 16px rgba(154,133,107,0.3)',
                    letterSpacing: 1,
                  }}
                >
                  PULL THE LEVER
                </Button>
              )}
              {phase === 'spinning' && (
                <Text style={{
                  color: C.primary, fontSize: '1.1rem', fontWeight: 600,
                  animation: 'pulseGlow 1s ease-in-out infinite',
                  display: 'inline-block', padding: '12px 24px',
                }}>
                  Spinning...
                </Text>
              )}
              {phase === 'won' && (
                <Button
                  type="primary"
                  size="large"
                  icon={<GiftOutlined />}
                  onClick={handleRevealPrize}
                  style={{
                    height: 56,
                    fontSize: '1.2rem',
                    fontWeight: 700,
                    paddingInline: 48,
                    background: `linear-gradient(135deg, ${C.primaryLight}, ${C.primary})`,
                    border: 'none',
                    color: '#ffffff',
                    boxShadow: '0 4px 16px rgba(154,133,107,0.35)',
                    letterSpacing: 1,
                  }}
                >
                  REVEAL YOUR PRIZE
                </Button>
              )}
            </div>
          </div>
        )}

        {/* ── PDF Viewer ── */}
        {phase === 'pdf' && (
          <div
            style={{
              width: '100%',
              maxWidth: 900,
              zIndex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 20,
            }}
          >
            {pdfError ? (
              <div style={{
                background: C.container,
                border: `1px solid ${C.border}`,
                borderRadius: 12,
                padding: '40px 24px',
                textAlign: 'center',
                width: '100%',
                boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
              }}>
                <Title level={3} style={{ color: C.textSecondary, margin: 0 }}>
                  Prize PDF Not Yet Available
                </Title>
                <Text style={{ color: C.textMuted, display: 'block', marginTop: 8 }}>
                  The prize document hasn't been uploaded yet. Please check back later!
                </Text>
              </div>
            ) : pdfUrl ? (
              <>
                <div
                  style={{
                    width: '100%',
                    height: '70vh',
                    borderRadius: 12,
                    overflow: 'hidden',
                    border: `2px solid ${C.border}`,
                    boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
                  }}
                >
                  <iframe
                    src={pdfUrl}
                    style={{ width: '100%', height: '100%', border: 'none' }}
                    title="Prize Document"
                  />
                </div>
                <Button
                  type="primary"
                  size="large"
                  icon={<DownloadOutlined />}
                  onClick={handleDownload}
                  style={{
                    height: 56,
                    fontSize: '1.2rem',
                    fontWeight: 700,
                    paddingInline: 48,
                    background: `linear-gradient(135deg, ${C.primary}, ${C.primaryDark})`,
                    border: 'none',
                    color: '#ffffff',
                    boxShadow: '0 4px 16px rgba(154,133,107,0.3)',
                    letterSpacing: 1,
                  }}
                >
                  DOWNLOAD PRIZE
                </Button>
              </>
            ) : (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <div style={{
                  width: 48, height: 48, border: `4px solid ${C.primary}`,
                  borderTopColor: 'transparent', borderRadius: '50%',
                  animation: 'spin 1s linear infinite', margin: '0 auto 16px',
                }} />
                <Text style={{ color: C.textSecondary, fontSize: '1.1rem' }}>
                  Loading your prize...
                </Text>
              </div>
            )}
          </div>
        )}

        {/* Footer */}
        <div style={{
          position: 'absolute', bottom: 16, color: C.borderLight,
          fontSize: '0.75rem', letterSpacing: 1,
        }}>
          River Delta Fund Group
        </div>
      </div>
    </ConfigProvider>
  )
}
