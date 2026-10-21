import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import * as THREE from 'three';
import {
  X,
  Play,
  Pause,
  Volume2,
  Shield,
  Sparkles,
  Box,
  Cpu,
  UserCheck,
} from 'lucide-react';
import { MomentData } from '../../types/daycube';
import { SAMPLE_MOMENTS } from '../../data/sampleDay';
import { COLORS, FONTS, glassPanelStyle, pillButtonStyle } from '../../styles/theme';

interface MomentPortalModalProps {
  momentId: string | null;
  onClose: () => void;
  speakerOverrides: Record<string, string>;
  onConfirmSpeaker: (diarLabel: string, personName: string) => void;
}

// ============================================================================
// THREE.JS 2.5D DISPLACED DEPTH MESH + TIER 2 GAUSSIAN SPLAT CLOUD
// ============================================================================
const Moment3DScene: React.FC<{
  moment: MomentData;
  renderMode: '2.5d' | 'splat';
  currentTimeS: number;
  privacyBlur: boolean;
  speakerOverrides: Record<string, string>;
}> = ({ moment, renderMode, currentTimeS, privacyBlur, speakerOverrides }) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const splatRef = useRef<THREE.Points>(null);

  // Build 2.5D displaced PlaneGeometry (64x36 grid for ultra-fast zero-lag vertex relief)
  const displacedGeo = useMemo(() => {
    const geo = new THREE.PlaneGeometry(7.2, 4.2, 64, 36);
    const pos = geo.attributes.position;
    const colors = new Float32Array(pos.count * 3);
    const c1 = new THREE.Color(moment.palette[0]);
    const c2 = new THREE.Color(moment.palette[1]);
    const c3 = new THREE.Color(moment.palette[2]);

    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vy = pos.getY(i);
      // Procedural metric depth map simulation (table foreground at +0.7m, background walls at -0.6m)
      const tableBump = Math.exp(-((vx * vx) / 3.5 + ((vy + 0.8) * (vy + 0.8)) / 1.2)) * 0.85;
      const personLeft = Math.exp(-(((vx + 1.5) ** 2) / 0.65 + ((vy - 0.1) ** 2) / 0.9)) * 0.95;
      const personRight = Math.exp(-(((vx - 1.5) ** 2) / 0.65 + ((vy - 0.1) ** 2) / 0.9)) * 0.9;
      const archWall = Math.sin(vx * 1.2) * 0.18 - 0.35;
      const zDepth = tableBump + personLeft + personRight + archWall;
      pos.setZ(i, zDepth);

      const t = Math.min(1, Math.max(0, (zDepth + 0.5) / 1.8));
      const mixed = c1.clone().lerp(c2, t).lerp(c3, personLeft + personRight > 0.3 ? 0.45 : 0);
      colors[i * 3] = mixed.r;
      colors[i * 3 + 1] = mixed.g;
      colors[i * 3 + 2] = mixed.b;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.computeVertexNormals();
    return geo;
  }, [moment]);

  // Build Tier 2 Gaussian Splat Point Cloud (3,600 splats)
  const splatGeo = useMemo(() => {
    const count = 3600;
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const c1 = new THREE.Color(moment.palette[0]);
    const c2 = new THREE.Color(moment.palette[1]);
    const c3 = new THREE.Color(moment.palette[2]);

    for (let i = 0; i < count; i++) {
      const vx = (Math.random() - 0.5) * 7.4;
      const vy = (Math.random() - 0.5) * 4.3;
      const tableBump = Math.exp(-((vx * vx) / 3.5 + ((vy + 0.8) * (vy + 0.8)) / 1.2)) * 0.85;
      const personLeft = Math.exp(-(((vx + 1.5) ** 2) / 0.65 + ((vy - 0.1) ** 2) / 0.9)) * 0.95;
      const personRight = Math.exp(-(((vx - 1.5) ** 2) / 0.65 + ((vy - 0.1) ** 2) / 0.9)) * 0.9;
      const vz = tableBump + personLeft + personRight + (Math.random() - 0.5) * 0.22 - 0.25;

      positions[i * 3] = vx;
      positions[i * 3 + 1] = vy;
      positions[i * 3 + 2] = vz;

      const t = Math.min(1, Math.max(0, (vz + 0.5) / 1.8));
      const col = c1.clone().lerp(c2, t).lerp(c3, Math.random() * 0.35);
      colors[i * 3] = col.r;
      colors[i * 3 + 1] = col.g;
      colors[i * 3 + 2] = col.b;
    }
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    return geo;
  }, [moment]);

  const activeSeg = moment.transcript.find(
    (s) => currentTimeS >= s.start && currentTimeS <= s.end
  );

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    if (meshRef.current) {
      meshRef.current.rotation.y = Math.sin(t * 0.45) * 0.06;
    }
    if (splatRef.current) {
      splatRef.current.rotation.y = Math.sin(t * 0.45) * 0.06;
    }
  });

  return (
    <group>
      <ambientLight intensity={0.9} />
      <directionalLight position={[4, 6, 5]} intensity={1.5} />

      {renderMode === '2.5d' ? (
        <mesh ref={meshRef} geometry={displacedGeo}>
          <meshStandardMaterial
            vertexColors
            roughness={0.55}
            metalness={0.1}
            flatShading
            wireframe={false}
          />
        </mesh>
      ) : (
        <points ref={splatRef} geometry={splatGeo}>
          <pointsMaterial size={0.09} vertexColors transparent opacity={0.9} />
        </points>
      )}

      {/* Spatial Speaker Billboards at x = ±1.5m (Section 5.7) */}
      {moment.speakers.map((sp) => {
        const xPos = sp.side === 'left' ? -1.55 : sp.side === 'right' ? 1.55 : 0;
        const isTalking = activeSeg?.speaker === sp.diarLabel;
        const name = speakerOverrides[sp.diarLabel] || sp.displayName;
        return (
          <group key={sp.diarLabel} position={[xPos, 0.25, 0.85]}>
            <mesh>
              <ringGeometry args={[0.38, 0.46, 32]} />
              <meshBasicMaterial
                color={isTalking ? COLORS.amber : sp.color}
                side={THREE.DoubleSide}
              />
            </mesh>
            <Html position={[0, 0.68, 0]} center>
              <div
                style={{
                  padding: '4px 10px',
                  borderRadius: 999,
                  background: isTalking ? COLORS.amber : 'rgba(12, 16, 26, 0.9)',
                  color: isTalking ? '#090b10' : COLORS.textPrimary,
                  border: `1px solid ${sp.color}`,
                  fontFamily: FONTS.mono,
                  fontSize: 10.5,
                  fontWeight: 700,
                  whiteSpace: 'nowrap',
                  boxShadow: isTalking ? `0 0 20px ${COLORS.amber}` : 'none',
                }}
              >
                {name} ({sp.side.toUpperCase()} HRTF) {isTalking ? '· SPEAKING' : ''}
              </div>
            </Html>
          </group>
        );
      })}

      {/* Privacy Face-Blur Indicator for Background Bystanders (Section 5.6) */}
      {privacyBlur && (
        <Html position={[0.1, 1.15, -0.1]} center>
          <div
            style={{
              padding: '4px 9px',
              borderRadius: 999,
              background: 'rgba(16, 185, 129, 0.18)',
              border: `1px solid ${COLORS.emerald}`,
              backdropFilter: 'blur(12px)',
              color: '#a7f3d0',
              fontFamily: FONTS.mono,
              fontSize: 9.5,
              whiteSpace: 'nowrap',
            }}
          >
            ✓ {moment.faceBlurCount} Bystander Faces Blurred (Gaussian k=31)
          </div>
        </Html>
      )}
    </group>
  );
};

export const MomentPortalModal: React.FC<MomentPortalModalProps> = ({
  momentId,
  onClose,
  speakerOverrides,
  onConfirmSpeaker,
}) => {
  const moment = momentId ? SAMPLE_MOMENTS[momentId] || SAMPLE_MOMENTS.mo_003 : null;
  const [renderMode, setRenderMode] = useState<'2.5d' | 'splat'>(
    moment?.tier === 'splat' ? 'splat' : '2.5d'
  );
  const [playing, setPlaying] = useState(false);
  const [currentTimeS, setCurrentTimeS] = useState(2.5);
  const [privacyBlur, setPrivacyBlur] = useState(true);
  const audioCtxRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    if (moment) {
      setRenderMode(moment.tier === 'splat' ? 'splat' : '2.5d');
      setCurrentTimeS(moment.transcript[0]?.start || 1.5);
    }
  }, [moment]);

  // Trigger subtle HRTF spatial audio cue when a segment plays (Section 5.7 & 8.4)
  const triggerSpatialAudioCue = (side: 'left' | 'center' | 'right') => {
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
      }
      const ctx = audioCtxRef.current;
      if (ctx.state === 'suspended') ctx.resume();

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const panner = ctx.createPanner();
      panner.panningModel = 'HRTF';
      panner.positionX.value = side === 'left' ? -1.5 : side === 'right' ? 1.5 : 0;
      panner.positionY.value = 0;
      panner.positionZ.value = -1;

      osc.type = 'sine';
      osc.frequency.value = side === 'left' ? 320 : 410;
      gain.gain.setValueAtTime(0.04, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);

      osc.connect(panner);
      panner.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch {
      // Ignore if AudioContext is blocked
    }
  };

  useEffect(() => {
    if (!playing || !moment) return;
    const interval = setInterval(() => {
      setCurrentTimeS((prev) => {
        const next = prev + 0.2;
        if (next >= moment.durationS) {
          setPlaying(false);
          return 0;
        }
        return next;
      });
    }, 200);
    return () => clearInterval(interval);
  }, [playing, moment]);

  if (!moment) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 70,
        background: 'rgba(5, 7, 12, 0.82)',
        backdropFilter: 'blur(14px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={glassPanelStyle({
          width: '100%',
          maxWidth: 1020,
          height: '86vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: `1px solid ${COLORS.borderStrong}`,
        })}
      >
        {/* Top Bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 20px',
            borderBottom: `1px solid ${COLORS.borderSubtle}`,
          }}
        >
          <div>
            <div
              style={{
                fontFamily: FONTS.mono,
                fontSize: 10.5,
                color: COLORS.amber,
                fontWeight: 700,
              }}
            >
              MOMENT PORTAL · {moment.id.toUpperCase()} · {moment.placeName.toUpperCase()} (FLOOR{' '}
              {moment.floor})
            </div>
            <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>{moment.title}</div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              onClick={() => setRenderMode('2.5d')}
              style={pillButtonStyle(renderMode === '2.5d', COLORS.amber)}
            >
              <Box size={13} /> Tier 1: 2.5D Depth Mesh (±25° Clamp)
            </button>
            <button
              type="button"
              onClick={() => setRenderMode('splat')}
              style={pillButtonStyle(renderMode === 'splat', COLORS.sky)}
            >
              <Sparkles size={13} /> Tier 2: 3D Gaussian Splat (.ply)
            </button>
            <button
              type="button"
              onClick={() => setPrivacyBlur((v) => !v)}
              style={pillButtonStyle(privacyBlur, COLORS.emerald)}
            >
              <Shield size={13} /> Consent Face Blur ({moment.faceBlurCount})
            </button>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: 'transparent',
                border: 'none',
                color: COLORS.textSecondary,
                cursor: 'pointer',
                padding: 6,
              }}
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* 3D Viewport (Clamped Orbit per Section 8.4) */}
        <div style={{ flex: 1, position: 'relative', background: '#07090f' }}>
          <Canvas camera={{ position: [0, 0, 5.2], fov: moment.cameraFovDeg }}>
            <Moment3DScene
              moment={moment}
              renderMode={renderMode}
              currentTimeS={currentTimeS}
              privacyBlur={privacyBlur}
              speakerOverrides={speakerOverrides}
            />
            {/* Clamped orbit: ±25° yaw (0.43 rad), ±15° pitch (0.26 rad) in 2.5D mode */}
            <OrbitControls
              enablePan={false}
              minAzimuthAngle={renderMode === '2.5d' ? -0.43 : -0.7}
              maxAzimuthAngle={renderMode === '2.5d' ? 0.43 : 0.7}
              minPolarAngle={Math.PI / 2 - 0.26}
              maxPolarAngle={Math.PI / 2 + 0.26}
              minDistance={3.2}
              maxDistance={7.5}
            />
          </Canvas>

          {/* Viewport HUD Overlay */}
          <div
            style={{
              position: 'absolute',
              top: 14,
              left: 16,
              padding: '8px 12px',
              borderRadius: 10,
              background: 'rgba(10, 14, 22, 0.84)',
              border: `1px solid ${COLORS.borderSubtle}`,
              fontFamily: FONTS.mono,
              fontSize: 10.5,
              color: COLORS.textSecondary,
              display: 'flex',
              flexDirection: 'column',
              gap: 3,
            }}
          >
            <div style={{ color: COLORS.amber, fontWeight: 700 }}>
              <Cpu size={11} style={{ display: 'inline', marginRight: 5 }} />
              {renderMode === '2.5d'
                ? `Depth-Anything-V2 · Keyframe #${moment.keyframeId} · Orbit Clamped ±25°`
                : `VGGT COLMAP + gsplat 7000 steps · ${moment.splatFile || 'scene.ply'}`}
            </div>
            <div>
              HRTF Spatial Audio: PannerNode @ x = ±1.5m · Drag to inspect 3D parallax
            </div>
          </div>
        </div>

        {/* Synchronized WhisperX Transcript Strip & Speaker Picker (Section 5.3 & 8.4) */}
        <div
          style={{
            padding: '14px 20px',
            background: 'rgba(10, 13, 20, 0.96)',
            borderTop: `1px solid ${COLORS.borderSubtle}`,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {/* Playback Control Row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              type="button"
              onClick={() => {
                setPlaying((p) => !p);
                triggerSpatialAudioCue('left');
              }}
              style={{
                padding: '7px 14px',
                borderRadius: 999,
                background: COLORS.amber,
                color: '#090b10',
                border: 'none',
                fontWeight: 700,
                fontSize: 12,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              {playing ? <Pause size={14} /> : <Play size={14} />}
              {playing ? 'Pause Moment' : 'Play Spatial Audio + Transcript'}
            </button>

            <input
              type="range"
              min={0}
              max={moment.durationS}
              step={0.1}
              value={currentTimeS}
              onChange={(e) => setCurrentTimeS(Number(e.target.value))}
              style={{ flex: 1, accentColor: COLORS.amber, cursor: 'pointer' }}
            />

            <span style={{ fontFamily: FONTS.mono, fontSize: 11.5, color: COLORS.amber }}>
              {currentTimeS.toFixed(1)}s / {moment.durationS.toFixed(1)}s
            </span>
          </div>

          {/* Diarized Transcript Segments */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
            {moment.transcript.map((seg) => {
              const spMeta = moment.speakers.find((s) => s.diarLabel === seg.speaker);
              const spName = speakerOverrides[seg.speaker] || spMeta?.displayName || seg.speaker;
              const isCurrent = currentTimeS >= seg.start && currentTimeS <= seg.end;

              return (
                <div
                  key={seg.id}
                  onClick={() => {
                    setCurrentTimeS(seg.start + 0.2);
                    triggerSpatialAudioCue(spMeta?.side || 'center');
                  }}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 10,
                    background: isCurrent ? 'rgba(255, 180, 84, 0.14)' : 'rgba(255,255,255,0.03)',
                    border: `1px solid ${isCurrent ? COLORS.amber : COLORS.borderSubtle}`,
                    cursor: 'pointer',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 5,
                    }}
                  >
                    <span
                      style={{
                        fontFamily: FONTS.mono,
                        fontSize: 10,
                        color: spMeta?.color || COLORS.amber,
                        fontWeight: 700,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 5,
                      }}
                    >
                      <Volume2 size={12} /> {spName} ({seg.speaker} · {spMeta?.side.toUpperCase()})
                    </span>
                    <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.textSecondary }}>
                      {seg.start.toFixed(1)}s–{seg.end.toFixed(1)}s
                    </span>
                  </div>

                  {/* Word-level highlighted transcript */}
                  <div style={{ fontSize: 12.5, lineHeight: 1.45 }}>
                    {seg.words.map((w, wi) => {
                      const activeWord = currentTimeS >= w.start && currentTimeS <= w.end + 0.3;
                      return (
                        <span
                          key={wi}
                          style={{
                            color: activeWord ? COLORS.amber : COLORS.textPrimary,
                            fontWeight: activeWord ? 700 : 400,
                            marginRight: 4,
                          }}
                        >
                          {w.word}
                        </span>
                      );
                    })}
                  </div>

                  {/* Quick Speaker Confirmation */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginTop: 7,
                      paddingTop: 5,
                      borderTop: '1px solid rgba(255,255,255,0.06)',
                    }}
                  >
                    <span style={{ fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.textMuted }}>
                      <UserCheck size={10} style={{ display: 'inline', marginRight: 3 }} />
                      Confirm {seg.speaker}:
                    </span>
                    <div style={{ display: 'flex', gap: 4 }}>
                      {['Sam', 'Maya', 'You'].map((person) => (
                        <button
                          key={person}
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onConfirmSpeaker(seg.speaker, person);
                          }}
                          style={{
                            padding: '1px 6px',
                            borderRadius: 4,
                            fontSize: 9.5,
                            fontFamily: FONTS.mono,
                            border:
                              spName === person
                                ? `1px solid ${COLORS.emerald}`
                                : `1px solid ${COLORS.borderSubtle}`,
                            background: spName === person ? COLORS.emeraldSoft : 'transparent',
                            color: spName === person ? COLORS.emerald : COLORS.textSecondary,
                            cursor: 'pointer',
                          }}
                        >
                          {person}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
