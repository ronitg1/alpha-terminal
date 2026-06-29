/**
 * WelcomeDialog — the multi-step first-login walkthrough popup.
 *
 * A carousel over WELCOME_SLIDES built on the shared Shadcn Dialog. Every step
 * has Back / Next and a persistent Skip, so a user is never trapped. The final
 * step offers "Take the tour" (launches the interactive driver.js tour) and
 * "Finish". Closing the dialog by any means counts as completing onboarding so
 * it does not auto-reopen; the Help button can replay it.
 *
 * Screenshots are optional: a slide with a missing/failed image shows a neutral
 * placeholder, so the walkthrough is fully functional before images exist.
 */
import { useState } from 'react';

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { WELCOME_SLIDES } from './onboarding-steps';

interface WelcomeDialogProps {
  open: boolean;
  /** Called when the dialog should close (Skip, Finish, X, or Esc). */
  onClose: () => void;
  /** Called when the user chooses to launch the interactive tour. */
  onStartTour: () => void;
}

/** Screenshot with a graceful placeholder when the image is absent. */
function SlideImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  const showImage = src && !failed;
  return (
    <div className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40">
      {showImage ? (
        <img
          src={src}
          alt={alt ?? ''}
          className="h-full w-full object-cover object-top"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="px-6 text-center text-xs text-muted-foreground">
          {alt ?? 'Screenshot'}
        </span>
      )}
    </div>
  );
}

export function WelcomeDialog({ open, onClose, onStartTour }: WelcomeDialogProps) {
  const [step, setStep] = useState(0);
  const total = WELCOME_SLIDES.length;
  const slide = WELCOME_SLIDES[step];
  const isFirst = step === 0;
  const isLast = step === total - 1;

  // Reset to the first slide whenever the dialog is opened fresh.
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      onClose();
      // Defer the reset so the closing animation does not flash slide 1.
      setTimeout(() => setStep(0), 200);
    }
  };

  const startTour = () => {
    handleOpenChange(false);
    onStartTour();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="sm:max-w-xl"
        // Don't let an accidental backdrop click discard the walkthrough.
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{slide.title}</DialogTitle>
        </DialogHeader>

        <SlideImage src={slide.image} alt={slide.imageAlt} />

        <div className="text-sm leading-relaxed">{slide.body}</div>

        {/* Progress dots */}
        <div className="flex justify-center gap-1.5 pt-1">
          {WELCOME_SLIDES.map((s, i) => (
            <button
              key={s.id}
              type="button"
              aria-label={`Go to step ${i + 1}`}
              onClick={() => setStep(i)}
              className={cn(
                'h-1.5 rounded-full transition-all',
                i === step ? 'w-5 bg-primary' : 'w-1.5 bg-muted-foreground/30 hover:bg-muted-foreground/60',
              )}
            />
          ))}
        </div>

        {/* Controls */}
        <div className="mt-2 flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => handleOpenChange(false)}>
            Skip
          </Button>

          <div className="flex items-center gap-2">
            <span className="mr-1 text-xs text-muted-foreground">
              {step + 1} / {total}
            </span>
            {!isFirst && (
              <Button variant="outline" size="sm" onClick={() => setStep((s) => s - 1)}>
                Back
              </Button>
            )}
            {!isLast ? (
              <Button size="sm" onClick={() => setStep((s) => s + 1)}>
                Next
              </Button>
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={startTour}>
                  Take the tour
                </Button>
                <Button size="sm" onClick={() => handleOpenChange(false)}>
                  Finish
                </Button>
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
