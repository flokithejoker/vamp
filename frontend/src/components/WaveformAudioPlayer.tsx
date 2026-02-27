import { useEffect, useRef, useState } from 'react';
import WaveSurfer from 'wavesurfer.js';

type WaveformAudioPlayerProps = {
  audioUrl: string;
};

function formatAudioTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '00:00';
  }

  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
}

export function WaveformAudioPlayer({ audioUrl }: WaveformAudioPlayerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const waveSurferRef = useRef<WaveSurfer | null>(null);
  const [isReady, setIsReady] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [hasWaveformError, setHasWaveformError] = useState(false);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    setIsReady(false);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setHasWaveformError(false);

    const waveSurfer = WaveSurfer.create({
      container: containerRef.current,
      url: audioUrl,
      waveColor: '#cbd5e1',
      progressColor: '#0ea5e9',
      cursorColor: '#0284c7',
      barWidth: 2,
      barGap: 1.5,
      barRadius: 2,
      height: 72,
      normalize: true,
      dragToSeek: true,
    });

    waveSurferRef.current = waveSurfer;

    const onReady = () => {
      setDuration(waveSurfer.getDuration());
      setCurrentTime(0);
      setIsReady(true);
      setHasWaveformError(false);
    };
    const onTimeUpdate = (seconds: number) => setCurrentTime(seconds);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onFinish = () => setIsPlaying(false);
    const onError = () => {
      setHasWaveformError(true);
      setIsReady(false);
      setIsPlaying(false);
    };

    waveSurfer.on('ready', onReady);
    waveSurfer.on('timeupdate', onTimeUpdate);
    waveSurfer.on('play', onPlay);
    waveSurfer.on('pause', onPause);
    waveSurfer.on('finish', onFinish);
    waveSurfer.on('error', onError);

    return () => {
      waveSurfer.un('ready', onReady);
      waveSurfer.un('timeupdate', onTimeUpdate);
      waveSurfer.un('play', onPlay);
      waveSurfer.un('pause', onPause);
      waveSurfer.un('finish', onFinish);
      waveSurfer.un('error', onError);
      waveSurfer.destroy();
      waveSurferRef.current = null;
    };
  }, [audioUrl]);

  function togglePlayback() {
    const waveSurfer = waveSurferRef.current;
    if (!waveSurfer || !isReady) {
      return;
    }
    void waveSurfer.playPause();
  }

  if (hasWaveformError) {
    return (
      <div className="monitoring-audio-fallback">
        <audio controls preload="none" src={audioUrl} className="monitoring-audio-player" />
        <p className="monitoring-audio-fallback-note">Waveform unavailable, using standard audio player.</p>
      </div>
    );
  }

  return (
    <div className="waveform-player">
      <button
        type="button"
        className="icon-button waveform-play-button"
        onClick={togglePlayback}
        disabled={!isReady}
        aria-label={isPlaying ? 'Pause audio' : 'Play audio'}
        title={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? (
          <svg viewBox="0 0 24 24" className="icon-16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M8 5v14" />
            <path d="M16 5v14" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="icon-16" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        )}
      </button>
      <div ref={containerRef} className="waveform-canvas" />
      <span className="waveform-time-chip">
        {formatAudioTime(currentTime)} / {formatAudioTime(duration)}
      </span>
    </div>
  );
}
