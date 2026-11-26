/** UI state shared between the DOM sections and the 3D scene, read every frame. */
export const ui = {
  /** The question shown in Ask, by id. */
  question: null as string | null,
  questionAt: 0,
};
