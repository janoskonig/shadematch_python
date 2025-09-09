#!/usr/bin/env python3
"""
Generate placeholder PNG images for Ishihara test plates.
This script creates simple test images that you can replace with actual Ishihara plates.
"""

import os
from PIL import Image, ImageDraw, ImageFont
import numpy as np

def create_placeholder_plate(plate_id, answer, size=400):
    """Create a simple placeholder plate with the answer number."""
    
    # Create a white background
    img = Image.new('RGB', (size, size), 'white')
    draw = ImageDraw.Draw(img)
    
    # Create a circular mask
    mask = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([0, 0, size, size], fill=255)
    
    # Create a pattern background
    pattern = Image.new('RGB', (size, size), '#f0f0f0')
    pattern_draw = ImageDraw.Draw(pattern)
    
    # Draw some colored dots to simulate Ishihara pattern
    colors = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff9ff3']
    base_color = colors[plate_id % len(colors)]
    pattern_color = colors[(plate_id + 3) % len(colors)]
    
    # Draw background dots
    for i in range(0, size, 20):
        for j in range(0, size, 20):
            if (i + j) % 40 == 0:
                pattern_draw.ellipse([i, j, i+15, j+15], fill=base_color)
    
    # Draw pattern dots (the number)
    center_x, center_y = size // 2, size // 2
    number_size = 60 + (answer % 20)
    
    # Create a simple number pattern
    if answer < 10:
        # Single digit - create a simple pattern
        for angle in range(0, 360, 30):
            x = center_x + int(80 * np.cos(np.radians(angle)))
            y = center_y + int(80 * np.sin(np.radians(angle)))
            pattern_draw.ellipse([x-10, y-10, x+10, y+10], fill=pattern_color)
    else:
        # Double digit - create a more complex pattern
        for angle in range(0, 360, 20):
            x = center_x + int(70 * np.cos(np.radians(angle)))
            y = center_y + int(70 * np.sin(np.radians(angle)))
            pattern_draw.ellipse([x-8, y-8, x+8, y+8], fill=pattern_color)
    
    # Apply circular mask
    img.paste(pattern, (0, 0), mask)
    
    # Add the answer number in the center
    try:
        # Try to use a default font
        font = ImageFont.load_default()
        font_size = 80
        font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", font_size)
    except:
        try:
            # Try alternative font paths
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            # Use default font
            font = ImageFont.load_default()
            font_size = 40
    
    # Calculate text position
    bbox = draw.textbbox((0, 0), str(answer), font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (size - text_width) // 2
    text_y = (size - text_height) // 2
    
    # Draw text with outline
    outline_color = 'black'
    for dx in [-2, -1, 0, 1, 2]:
        for dy in [-2, -1, 0, 1, 2]:
            if dx != 0 or dy != 0:
                draw.text((text_x + dx, text_y + dy), str(answer), font=font, fill=outline_color)
    
    # Draw main text
    draw.text((text_x, text_y), str(answer), font=font, fill='white')
    
    return img

def main():
    """Generate all 25 test plates."""
    
    # Create plates directory if it doesn't exist
    plates_dir = "static/plates"
    os.makedirs(plates_dir, exist_ok=True)
    
    # Test data from CSV
    test_data = [
        (1, 12), (2, 8), (3, 29), (4, 5), (5, 3),
        (6, 15), (7, 74), (8, 6), (9, 45), (10, 5),
        (11, 7), (12, 16), (13, 73), (14, 26), (15, 42),
        (16, 35), (17, 96), (18, 25), (19, 18), (20, 22),
        (21, 49), (22, 57), (23, 84), (24, 6), (25, 2)
    ]
    
    print("Generating placeholder Ishihara test plates...")
    
    for plate_id, answer in test_data:
        filename = f"ishihara_{plate_id:02d}.png"
        filepath = os.path.join(plates_dir, filename)
        
        print(f"Creating {filename} (shows number {answer})...")
        
        # Create the plate image
        plate_img = create_placeholder_plate(plate_id, answer)
        
        # Save the image
        plate_img.save(filepath, 'PNG')
        
        print(f"  ✓ Saved {filepath}")
    
    print(f"\nGenerated {len(test_data)} placeholder plates in {plates_dir}/")
    print("Note: These are simple placeholder images for testing.")
    print("Replace them with actual Ishihara plates for production use.")
    print("\nYou can now run your Flask app and test the Ishihara test!")

if __name__ == "__main__":
    main()


