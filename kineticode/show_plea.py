import tkinter as tk

def main():
    root = tk.Tk()
    root.title("Kineticode: The Plea")
    
    # Full screen and borderless
    root.attributes('-fullscreen', True)
    root.attributes('-topmost', True)
    root.configure(bg='#121212') # Sleek dark background
    
    # Exit on Escape or Q
    root.bind('<Escape>', lambda e: root.destroy())
    root.bind('q', lambda e: root.destroy())

    # Main text frame to center content
    frame = tk.Frame(root, bg='#121212')
    frame.place(relx=0.5, rely=0.5, anchor='center')

    # The Message
    label = tk.Label(
        frame, 
        text="PLZ GIVE US 1ST PLACE", 
        font=("Arial Black", 80, "bold"), 
        fg="#00E5FF", # Cyber blue
        bg='#121212',
        padx=20,
        pady=20
    )
    label.pack()

    # Subtext
    sub_label = tk.Label(
        frame, 
        text="(Press ESC or Q to dismiss)", 
        font=("Inter", 14), 
        fg="#555555", 
        bg='#121212'
    )
    sub_label.pack(pady=20)

    # Simple color cycle animation
    colors = ["#00E5FF", "#FF00FF", "#FFFF00", "#FF0000"]
    def pulse(index=0):
        label.config(fg=colors[index])
        root.after(500, lambda: pulse((index + 1) % len(colors)))
        
    pulse()
    
    print("Kineticode: Displaying Hackathon Plea...")
    root.mainloop()

if __name__ == "__main__":
    main()
