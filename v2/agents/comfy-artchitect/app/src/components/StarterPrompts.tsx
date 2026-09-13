/* Four ways in, under the composer — the empty chat's only instruction.
 *
 * WHAT THESE REPLACED, and why. The opening used to be four big cards in a grid: "Check the
 * connection", "See what is installed", "Build a workflow", "Make one faster". Every one of them
 * was written when the user brought their own ComfyUI box and the first job was finding out what
 * it had. That world is gone — the instance is provisioned by `gpu_ensure`, there is no URL to
 * check and no inventory worth reciting before a design exists (AGENTS.md rule 16 forbids
 * shaping a job around what happens to be installed). So the cards offered four openings, three
 * of which asked the agent to do something it is now told not to do.
 *
 * THEY NAME THE JOB, NOT THE TOOL. What somebody opens this window to get is a video, an ad, a
 * character that stays the same person across shots. "See what is installed" is a step; these are
 * outcomes, which is the only thing a person can pick between before they know how any of it
 * works.
 *
 * THEY SEED, THEY DO NOT SEND. The chip fills the composer and leaves the cursor there, because
 * every one of these wants a detail added — which product, which person, how long — and a prompt
 * that fired on click would spend a turn on the generic version of the job. Same reason the
 * cards seeded, kept deliberately.
 */

const STARTERS: { label: string; prompt: string }[] = [
  {
    label: 'Realistic AI influencer',
    prompt:
      'Build a workflow for a photorealistic AI influencer — the same face in every shot. ' +
      "I'll give you a reference photo.",
  },
  {
    label: 'Product ad video',
    prompt: 'Turn a product photo into a short vertical ad video for social.',
  },
  {
    label: 'Same person, new angles',
    prompt:
      'Take one reference photo and give me the same person from four angles, ' +
      'keeping the face and the outfit consistent.',
  },
  {
    label: 'Animate a still',
    prompt: "Build an image-to-video workflow and animate a still I'll give you.",
  },
]

export function StarterPrompts({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="starters">
      {STARTERS.map((s) => (
        <button
          key={s.label}
          type="button"
          className="starter"
          /* The full prompt on hover: the chip is four words and what it sends is two lines, and
             a control that types something you did not read is a control you stop trusting. */
          title={s.prompt}
          onClick={() => onPick(s.prompt)}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}
