import React, { useEffect, useRef, useState, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import * as THREE from 'three';
import { Pause, Play, X } from 'lucide-react';
import type { MomentData } from '../../types/daycube';
import { SAMPLE_MOMENTS } from '../../data/sampleDay';
import { COLORS, formatTod } from '../../styles/theme';
import { Dialog } from '../ui';

interface MomentPortalModalProps {
  momentId: string | null;
  onClose: () => void;
  speakerOverrides: Record<string, string>;
  onConfirmSpeaker: (diarLabel: string, personName: string) => void;
}

const PEOPLE = ['Sam', 'Maya', 'You'];

function clockS(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

// ============================================================================
// THREE.JS 2.5D DISPLACED DEPTH MESH + TIER 2 GAUSSIAN SPLAT CLOUD
// ============================================================================
const Moment3DScene: React.FC<{
  moment: MomentData;
  renderMode: '2.5d' | 'splat';
  currentTimeS: number;
  speakerOverrides: Record<string, string>;
}> = ({ moment, renderMode, currentTimeS, speakerOverrides }) => {
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


      {moment.speakers.map((sp) => {
        const xPos = sp.side === 'left' ? -1.55 : sp.side === 'right' ? 1.55 : 0;
        const isTalking = activeSeg?.speaker === sp.diarLabel;
        const name = speakerOverrides[sp.diarLabel] || sp.displayName;
        return (
          <group key={sp.diarLabel} position={[xPos, 0.25, 0.85]}>
            <mesh>
              <ringGeometry args={[0.38, 0.44, 48]} />
              <meshBasicMaterial
                color={isTalking ? COLORS.accent : '#ffffff'}
                transparent
                opacity={isTalking ? 1 : 0.35}
                side={THREE.DoubleSide}
              />
            </mesh>
            <Html position={[0, 0.68, 0]} center zIndexRange={[4, 1]} style={{ pointerEvents: 'none' }}>
              <div className={`scene-label${isTalking ? ' scene-label--active' : ''}`}>
                <strong style={{ marginRight: isTalking ? 6 : 0 }}>{name}</strong>
                {isTalking ? 'speaking' : ''}
              </div>
            </Html>
          </group>
        );
      })}
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
  const [renderMode, setRenderMode] = useState<'2.5d' | 'splat'>(moment?.tier === 'splat' ? 'splat' : '2.5d');
  const [playing, setPlaying] = useState(false);
  const [currentTimeS, setCurrentTimeS] = useState(2.5);
  const [privacyBlur, setPrivacyBlur] = useState(true);
  const [picking, setPicking] = useState<string | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const activeRowRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (moment) {
      setRenderMode(moment.tier === 'splat' ? 'splat' : '2.5d');
      setCurrentTimeS(moment.transcript[0]?.start || 1.5);
    }
  }, [moment]);

  // A short panned tone so the speaker's side is audible (spatial audio placeholder).
  const playSideCue = (side: 'left' | 'center' | 'right') => {
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
      // Audio can be blocked until the page has had a user gesture.
    }
  };

  useEffect(() => {
    if (!playing || !moment) return;
    const interval = window.setInterval(() => {
      setCurrentTimeS((prev) => {
        const next = prev + 0.2;
        if (next >= moment.durationS) {
          setPlaying(false);
          return 0;
        }
        return next;
      });
    }, 200);
    return () => window.clearInterval(interval);
  }, [playing, moment]);

  const activeSegId = moment?.transcript.find((s) => currentTimeS >= s.start && currentTimeS <= s.end)?.id;
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activeSegId]);

  if (!moment) return null;

  return (
    <Dialog onClose={onClose} title={moment.title} className="viewer">
      <div className="dialog__head">
        <div>
          <h2 className="dialog__title">{moment.title}</h2>
          <div className="dialog__sub num">
            {moment.placeName}, {formatTod(moment.t)}, {Math.round(moment.durationS)} s
            {moment.floor ? `, floor ${moment.floor}` : ''}
          </div>
        </div>
        <div className="row">
          <span className="tag tag--illustrative" title="Generated from sample data. No source video is stored for this moment yet.">
            Sample reconstruction
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="viewer__body">
        <div className="viewer__main">
          <Canvas camera={{ position: [0, 0, 5.2], fov: moment.cameraFovDeg }}>
            <Moment3DScene
              moment={moment}
              renderMode={renderMode}
              currentTimeS={currentTimeS}
              speakerOverrides={speakerOverrides}
            />
            {/* Orbit clamped to the captured view: ±25° yaw, ±15° pitch for depth scenes. */}
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

          <div className="viewer__overlay viewer__overlay--tl">
            <div className="seg seg--sm" role="group" aria-label="Scene type">
              <button
                type="button"
                className="seg__item"
                aria-pressed={renderMode === '2.5d'}
                title="A single frame lifted into depth. Most accurate from the original angle."
                onClick={() => setRenderMode('2.5d')}
              >
                Depth
              </button>
              <button
                type="button"
                className="seg__item"
                aria-pressed={renderMode === 'splat'}
                title="A point-based 3D reconstruction from the whole clip. Allows a wider orbit."
                onClick={() => setRenderMode('splat')}
              >
                Splat
              </button>
            </div>
          </div>

          <div className="viewer__overlay viewer__overlay--tr">
            <div className="float" style={{ padding: '0 4px' }}>
              <button
                type="button"
                role="switch"
                aria-checked={privacyBlur}
                className="switch-row"
                style={{ padding: '6px 8px', gap: 10 }}
                onClick={() => setPrivacyBlur((v) => !v)}
              >
                <span className="switch-row__label" style={{ fontSize: 12.5 }}>
                  Blur bystanders{privacyBlur && moment.faceBlurCount ? ` (${moment.faceBlurCount})` : ''}
                </span>
                <span className="switch" aria-hidden />
              </button>
            </div>
          </div>
        </div>

        <div className="viewer__side">
          <div className="section" style={{ paddingBottom: 12 }}>
            <h3 className="section__title" style={{ margin: 0 }}>
              Transcript
            </h3>
          </div>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {moment.transcript.map((seg) => {
              const meta = moment.speakers.find((s) => s.diarLabel === seg.speaker);
              const name = speakerOverrides[seg.speaker] || meta?.displayName || seg.speaker;
              const isCurrent = seg.id === activeSegId;
              const isYou = /^you\b/i.test(name);
              return (
                <div key={seg.id}>
                  <button
                    ref={isCurrent ? activeRowRef : undefined}
                    type="button"
                    className="seg-row"
                    aria-current={isCurrent}
                    onClick={() => {
                      setCurrentTimeS(seg.start + 0.2);
                      playSideCue(meta?.side || 'center');
                    }}
                  >
                    <span className={`avatar${isYou ? ' avatar--you' : ''}`} aria-hidden>
                      {name.slice(0, 1).toUpperCase()}
                    </span>
                    <span>
                      <span className="quote__head">
                        <span className="quote__who">{name}</span>
                        <span className="faint num">{clockS(seg.start)}</span>
                      </span>
                      <span className="quote__text" style={{ display: 'block' }}>
                        {seg.words.map((w, wi) => (
                          <span
                            key={wi}
                            className={currentTimeS >= w.start && currentTimeS <= w.end + 0.3 ? 'word--active' : undefined}
                          >
                            {w.word}{' '}
                          </span>
                        ))}
                      </span>
                    </span>
                  </button>
                  <div style={{ padding: '0 16px 10px 52px' }}>
                    {picking === seg.id ? (
                      <div className="speaker-pick" style={{ marginTop: 0 }} role="group" aria-label="Who is speaking?">
                        <span>Who is this?</span>
                        {PEOPLE.map((p) => (
                          <button
                            key={p}
                            type="button"
                            className="chip"
                            aria-pressed={name === p}
                            onClick={() => {
                              onConfirmSpeaker(seg.speaker, p);
                              setPicking(null);
                            }}
                          >
                            {p}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        style={{ height: 22, marginLeft: -10, color: 'var(--fg-3)' }}
                        onClick={() => setPicking(seg.id)}
                      >
                        Wrong speaker?
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="viewer__bar">
        <button
          type="button"
          className="play-btn"
          style={{ width: 32, height: 32 }}
          aria-label={playing ? 'Pause' : 'Play'}
          onClick={() => {
            setPlaying((p) => !p);
            if (!playing) playSideCue('left');
          }}
        >
          {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" style={{ marginLeft: 2 }} />}
        </button>
        <input
          className="scrubber"
          type="range"
          min={0}
          max={moment.durationS}
          step={0.1}
          value={currentTimeS}
          aria-label="Position in moment"
          onChange={(e) => setCurrentTimeS(Number(e.target.value))}
        />
        <span className="num muted" style={{ fontSize: 12.5, minWidth: 74, textAlign: 'right' }}>
          {clockS(currentTimeS)} / {clockS(moment.durationS)}
        </span>
      </div>
    </Dialog>
  );
};
